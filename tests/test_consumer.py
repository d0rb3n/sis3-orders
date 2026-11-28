import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
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

    def run_and_capture(self, event):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = consumer.handler(event, None)
        lines = [json.loads(line) for line in buffer.getvalue().splitlines()]
        return result, lines

    def test_emits_embedded_metric_with_one_dimension(self):
        result, lines = self.run_and_capture({
            "Records": [record("good"), record("bad", quantity=0)],
        })

        metric_line = next(line for line in lines if "_aws" in line)
        definition = metric_line["_aws"]["CloudWatchMetrics"][0]
        self.assertEqual(definition["Namespace"], "CourseProject/Orders")
        self.assertEqual(definition["Dimensions"], [["Stage"]])
        self.assertEqual(metric_line["OrdersProcessed"], 1)
        self.assertEqual(metric_line["OrdersFailed"], 1)
        self.assertNotIn("orderId", metric_line)

    def test_failure_is_logged_as_structured_json(self):
        result, lines = self.run_and_capture({
            "Records": [record("bad", quantity=0)],
        })

        failed = next(line for line in lines if line.get("event") == "order_failed")
        self.assertEqual(failed["level"], "ERROR")
        self.assertEqual(failed["errorType"], "ValueError")
        self.assertEqual(failed["messageId"], "bad")


if __name__ == "__main__":
    unittest.main()
