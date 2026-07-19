from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency yielding an async DB session per-request."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to an existing development database.
        # Add the normalized product-category FK in place so current data can be
        # backfilled at application startup without requiring a destructive reset.
        product_tables = (
            "sales_records",
            "inventory_items",
            "inventory_movements",
            "stock_out_events",
            "promotion_campaigns",
            "delivery_schedules",
            "demand_forecasts",
        )

        def add_category_foreign_keys(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            for table in product_tables:
                columns = {column["name"] for column in inspector.get_columns(table)}
                if "category_id" not in columns:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN category_id "
                        "INTEGER REFERENCES product_categories(id)"
                    )
                sync_conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_category_id "
                    f"ON {table} (category_id)"
                )

        await conn.run_sync(add_category_foreign_keys)

        def add_task_explanation_columns(sync_conn):
            from sqlalchemy import inspect

            columns = {
                column["name"]
                for column in inspect(sync_conn).get_columns("tasks")
            }
            additions = {
                "priority_reason_type": "VARCHAR(60) NOT NULL DEFAULT ''",
                "priority_reason": "TEXT NOT NULL DEFAULT ''",
                "prioritized_by": "VARCHAR(40) NOT NULL DEFAULT ''",
                "prioritization_model": "VARCHAR(120)",
                "prioritized_at": "DATETIME",
                "completed_at": "DATETIME",
            }
            for name, definition in additions.items():
                if name not in columns:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE tasks ADD COLUMN {name} {definition}"
                    )

        await conn.run_sync(add_task_explanation_columns)

        def add_outlet_profile_columns(sync_conn):
            from sqlalchemy import inspect

            columns = {
                column["name"]
                for column in inspect(sync_conn).get_columns("outlets")
            }
            additions = {
                "address": "VARCHAR(240) NOT NULL DEFAULT ''",
                "contact_phone": "VARCHAR(40) NOT NULL DEFAULT ''",
                "contact_email": "VARCHAR(120) NOT NULL DEFAULT ''",
                "opening_date": "DATE",
                "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            }
            for name, definition in additions.items():
                if name not in columns:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE outlets ADD COLUMN {name} {definition}"
                    )

        await conn.run_sync(add_outlet_profile_columns)

        def ensure_normalized_employee_attendance(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            tables = set(inspector.get_table_names())
            if "employee_attendance" in tables:
                columns = {
                    column["name"]
                    for column in inspector.get_columns("employee_attendance")
                }
                if "employee_id" not in columns:
                    sync_conn.exec_driver_sql("DROP TABLE employee_attendance")

            sync_conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER NOT NULL,
                    outlet_id INTEGER NOT NULL,
                    employee_code VARCHAR(30) NOT NULL,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(120) NOT NULL,
                    phone VARCHAR(40) NOT NULL DEFAULT '',
                    designation VARCHAR(80) NOT NULL,
                    hire_date DATE,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id),
                    FOREIGN KEY(outlet_id) REFERENCES outlets (id) ON DELETE RESTRICT,
                    UNIQUE (employee_code),
                    UNIQUE (email)
                )
                """
            )

            # Shift assignment is not employee master data. Rebuild the small
            # employee table on SQLite so existing demo databases lose the
            # obsolete column without changing primary keys or attendance FKs.
            employee_columns = {
                column["name"] for column in inspect(sync_conn).get_columns("employees")
            }
            if "default_shift" in employee_columns:
                sync_conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                sync_conn.exec_driver_sql(
                    """
                    CREATE TABLE employees_without_default_shift (
                        id INTEGER NOT NULL PRIMARY KEY,
                        outlet_id INTEGER NOT NULL,
                        employee_code VARCHAR(30) NOT NULL UNIQUE,
                        name VARCHAR(120) NOT NULL,
                        email VARCHAR(120) NOT NULL UNIQUE,
                        phone VARCHAR(40) NOT NULL DEFAULT '',
                        designation VARCHAR(80) NOT NULL,
                        hire_date DATE,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        FOREIGN KEY(outlet_id) REFERENCES outlets (id) ON DELETE RESTRICT
                    )
                    """
                )
                sync_conn.exec_driver_sql(
                    """
                    INSERT INTO employees_without_default_shift
                        (id, outlet_id, employee_code, name, email, phone, designation,
                         hire_date, is_active, created_at, updated_at)
                    SELECT id, outlet_id, employee_code, name, email, phone, designation,
                           hire_date, is_active, created_at, updated_at
                    FROM employees
                    """
                )
                sync_conn.exec_driver_sql("DROP TABLE employees")
                sync_conn.exec_driver_sql("ALTER TABLE employees_without_default_shift RENAME TO employees")
                sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_employees_outlet_id ON employees (outlet_id)")
                sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_employees_employee_code ON employees (employee_code)")
                sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_employees_email ON employees (email)")
                sync_conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            sync_conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS employee_attendance (
                    id INTEGER NOT NULL,
                    employee_id INTEGER NOT NULL,
                    attendance_date DATE NOT NULL,
                    check_in_at DATETIME,
                    check_out_at DATETIME,
                    status VARCHAR(8) NOT NULL,
                    working_hours FLOAT NOT NULL DEFAULT 0.0,
                    remarks VARCHAR(180) NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_employee_attendance_employee_date
                        UNIQUE (employee_id, attendance_date),
                    FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE CASCADE
                )
                """
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employees_outlet_id ON employees (outlet_id)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employees_employee_code ON employees (employee_code)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employees_email ON employees (email)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employee_attendance_employee_id "
                "ON employee_attendance (employee_id)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employee_attendance_attendance_date "
                "ON employee_attendance (attendance_date)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_employee_attendance_employee_date "
                "ON employee_attendance (employee_id, attendance_date)"
            )

        await conn.run_sync(ensure_normalized_employee_attendance)

        def add_pos_order_columns(sync_conn):
            from sqlalchemy import inspect

            columns = {column["name"] for column in inspect(sync_conn).get_columns("pos_transactions")}
            additions = {
                "terminal_id": "INTEGER REFERENCES pos_terminals(id) ON DELETE RESTRICT",
                "customer_id": "INTEGER REFERENCES customers(id) ON DELETE SET NULL",
                "cashier_employee_id": "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
                "order_status": "VARCHAR(20) NOT NULL DEFAULT 'completed'",
                "payment_status": "VARCHAR(20) NOT NULL DEFAULT 'paid'",
                "paid_amount": "FLOAT NOT NULL DEFAULT 0.0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    sync_conn.exec_driver_sql(f"ALTER TABLE pos_transactions ADD COLUMN {name} {definition}")
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pos_transactions_terminal_id ON pos_transactions (terminal_id)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pos_transactions_customer_id ON pos_transactions (customer_id)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pos_transactions_cashier_employee_id ON pos_transactions (cashier_employee_id)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pos_transactions_order_status ON pos_transactions (order_status)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pos_transactions_payment_status ON pos_transactions (payment_status)"
            )

        await conn.run_sync(add_pos_order_columns)

        def add_payment_method_flags(sync_conn):
            from sqlalchemy import inspect

            columns = {column["name"] for column in inspect(sync_conn).get_columns("payment_methods")}
            if "is_digital" not in columns:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE payment_methods ADD COLUMN is_digital BOOLEAN NOT NULL DEFAULT 0"
                )

        await conn.run_sync(add_payment_method_flags)
