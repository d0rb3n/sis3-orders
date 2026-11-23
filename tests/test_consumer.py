import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

table = MagicMock()
source = Path(__file__).resolve().parents[1] / "consumer" / "handler.py"
spec = importlib.util.spec_from_file_location("consumer_handler", source)
consumer = importlib.util.module_from_spec(spec)

with patch.dict(os.environ, {"TABLE_NAME": "test-orders"}):
    with patch("boto3.resource") as resource:
        resource.return_value.Table.return_value = table
        spec.loader.exec_module(consumer)


def record(message_id, quantity=2):
    return {
        "messageId": message_id,
        "body": json.dumps({
            "eventType": "OrderPlaced",
            "orderId": "order-001",
            "product": "Notebook",
            "quantity": quantity,
        }),
    }


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        table.reset_mock()
        table.put_item.side_effect = None

    def test_valid_order(self):
        result = consumer.handler({"Records": [record("m1")]}, None)

        self.assertEqual(result, {"batchItemFailures": []})
        table.put_item.assert_called_once()
        args = table.put_item.call_args.kwargs
        self.assertEqual(args["Item"]["orderId"], "order-001")
        self.assertEqual(args["Item"]["status"], "PROCESSED")
        self.assertEqual(
            args["ConditionExpression"],
            "attribute_not_exists(orderId)",
        )

    def test_duplicate_is_acknowledged(self):
        table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )

        result = consumer.handler({"Records": [record("m2")]}, None)

        self.assertEqual(result, {"batchItemFailures": []})

    def test_only_invalid_message_is_retried(self):
        result = consumer.handler({
            "Records": [record("good"), record("bad", quantity=0)],
        }, None)

        self.assertEqual(result, {
            "batchItemFailures": [{"itemIdentifier": "bad"}],
        })
        table.put_item.assert_called_once()

    def test_database_error_is_retried(self):
        table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "PutItem",
        )

        result = consumer.handler({"Records": [record("m3")]}, None)

        self.assertEqual(result, {
            "batchItemFailures": [{"itemIdentifier": "m3"}],
        })


if __name__ == "__main__":
    unittest.main()
