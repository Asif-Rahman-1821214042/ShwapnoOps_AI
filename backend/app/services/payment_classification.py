"""Single source of truth for payment-method transaction categories."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PaymentMethod, PosPayment, PosTransaction, TransactionCategory


PAYMENT_CATEGORY_BY_CODE = {
    "cash": TransactionCategory.NORMAL_TRANSACTION,
    "card": TransactionCategory.POS_TRANSACTION,
    "bkash": TransactionCategory.E_PAYMENT,
    "nagad": TransactionCategory.E_PAYMENT,
    "rocket": TransactionCategory.E_PAYMENT,
    "bank_transfer": TransactionCategory.E_PAYMENT,
    "other": TransactionCategory.E_PAYMENT,
}


def classify_payment_method(method_code: str, has_pos_terminal: bool = True) -> TransactionCategory:
    code = method_code.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        category = PAYMENT_CATEGORY_BY_CODE[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported payment method: {method_code}") from exc
    if category is TransactionCategory.POS_TRANSACTION and not has_pos_terminal:
        raise ValueError("Card payments require a POS terminal for POS transaction classification")
    return category


async def migrate_payment_classifications(db: AsyncSession) -> None:
    """Backfill method and payment categories without changing the original method."""
    methods = (await db.execute(select(PaymentMethod))).scalars().all()
    for method in methods:
        method.transaction_category = classify_payment_method(method.code)
        method.is_digital = method.transaction_category is TransactionCategory.E_PAYMENT

    rows = (await db.execute(
        select(PosPayment, PaymentMethod, PosTransaction)
        .join(PaymentMethod, PosPayment.payment_method_id == PaymentMethod.id)
        .join(PosTransaction, PosPayment.transaction_id == PosTransaction.id)
    )).all()
    for payment, method, transaction in rows:
        payment.transaction_category = classify_payment_method(method.code, transaction.terminal_id is not None)
    await db.commit()
