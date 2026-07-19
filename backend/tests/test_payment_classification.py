import unittest

from app.models import TransactionCategory
from app.services.payment_classification import classify_payment_method


class PaymentClassificationTests(unittest.TestCase):
    def test_supported_method_categories(self):
        expected = {
            "cash": TransactionCategory.NORMAL_TRANSACTION,
            "card": TransactionCategory.POS_TRANSACTION,
            "bkash": TransactionCategory.E_PAYMENT,
            "nagad": TransactionCategory.E_PAYMENT,
            "rocket": TransactionCategory.E_PAYMENT,
            "bank_transfer": TransactionCategory.E_PAYMENT,
            "other": TransactionCategory.E_PAYMENT,
        }
        for method, category in expected.items():
            self.assertEqual(classify_payment_method(method), category)

    def test_card_requires_pos_terminal(self):
        with self.assertRaises(ValueError):
            classify_payment_method("card", has_pos_terminal=False)

    def test_unknown_method_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_payment_method("cheque")
