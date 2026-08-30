"""Tests for Micro-Agent Safe Side Effects."""

from micro_agent.observability import (
    Operation,
    OperationRegistry,
    OperationResult,
    RetryClassification,
)


class TestRetryClassification:
    """Test retry classification."""

    def test_values(self):
        assert RetryClassification.SAFE == "safe"
        assert RetryClassification.UNSAFE == "unsafe"
        assert RetryClassification.IDEMPOTENT == "idempotent"


class TestOperation:
    """Test operation model."""

    def test_defaults(self):
        op = Operation()
        assert op.operation_id
        assert op.idempotency_key is None
        assert op.retry_classification == RetryClassification.SAFE

    def test_with_idempotency_key(self):
        op = Operation(idempotency_key="key-123", name="payment")
        assert op.idempotency_key == "key-123"
        assert op.name == "payment"

    def test_requires_approval(self):
        op = Operation(requires_approval=True)
        assert op.requires_approval is True


class TestOperationResult:
    """Test operation result."""

    def test_success(self):
        result = OperationResult(operation_id="op-1", status="success")
        assert result.error is None
        assert result.was_deduplicated is False

    def test_deduplicated(self):
        result = OperationResult(was_deduplicated=True)
        assert result.was_deduplicated is True


class TestOperationRegistry:
    """Test operation registry for deduplication."""

    def test_record_and_find(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        result = OperationResult(operation_id=op.operation_id, status="success")
        registry.record(op, result)
        found = registry.find_by_idempotency_key("key-1")
        assert found is not None
        assert found.status == "success"

    def test_not_found(self):
        registry = OperationRegistry()
        assert registry.find_by_idempotency_key("missing") is None

    def test_is_duplicate(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        result = OperationResult(operation_id=op.operation_id, status="success")
        registry.record(op, result)
        assert registry.is_duplicate(op) is True

    def test_not_duplicate(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        assert registry.is_duplicate(op) is False

    def test_no_idempotency_key(self):
        registry = OperationRegistry()
        op = Operation(name="payment")
        assert registry.is_duplicate(op) is False
