"""
SettlementService 단위 테스트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Mock 없이 실제 SettlementService 인스턴스 사용
- 테스트마다 새 인스턴스를 만들어 인메모리 저장소가 공유되지 않게 함

실행: pytest -v test_settlement_service.py
"""

from datetime import datetime
from decimal import Decimal

import pytest

from settlement.models.models import Order, OrderStatus, SettlementStatus
from settlement.services.settlement_service import SettlementService

# ── 공통 픽스처 ──────────────────────────────────────────────────────

@pytest.fixture
def service() -> SettlementService:
    """테스트마다 깨끗한 서비스 인스턴스 (인메모리 저장소 초기화)"""
    return SettlementService()


@pytest.fixture
def june_period():
    """2026년 6월 정산 기간"""
    return datetime(2026, 6, 1, 0, 0, 0), datetime(2026, 6, 30, 23, 59, 59)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. list_settlements() — merchant_id + status 동시 필터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestListSettlementsCombinedFilter:

    @pytest.fixture
    def populated(self, service, june_period):
        """
        판매자 2명 × 상태 2종 조합의 정산 레코드를 실제 메서드로 생성

          M-001 : COMPLETED 1건, PENDING 1건
          M-002 : COMPLETED 1건, PENDING 1건
        """
        start, end = june_period
        records = {}
        for merchant in ("M-001", "M-002"):
            done = service.calculate_settlement(merchant, start, end)
            service.process_settlement(done.settlement_id)  # → COMPLETED
            pending = service.calculate_settlement(merchant, start, end)  # PENDING 유지
            records[(merchant, SettlementStatus.COMPLETED)] = done
            records[(merchant, SettlementStatus.PENDING)] = pending
        return service, records

    def test_merchant_and_completed_status(self, populated):
        service, records = populated

        result = service.list_settlements(
            merchant_id="M-001", status=SettlementStatus.COMPLETED
        )

        assert len(result) == 1
        assert result[0] is records[("M-001", SettlementStatus.COMPLETED)]
        assert result[0].merchant_id == "M-001"
        assert result[0].status == SettlementStatus.COMPLETED

    def test_merchant_and_pending_status(self, populated):
        service, records = populated

        result = service.list_settlements(
            merchant_id="M-002", status=SettlementStatus.PENDING
        )

        assert len(result) == 1
        assert result[0] is records[("M-002", SettlementStatus.PENDING)]

    def test_all_results_satisfy_both_conditions(self, populated):
        """반환된 모든 레코드가 두 조건을 동시에 만족해야 함 (OR가 아니라 AND)"""
        service, _ = populated

        result = service.list_settlements(
            merchant_id="M-001", status=SettlementStatus.PENDING
        )

        assert result  # 비어 있지 않음
        assert all(
            s.merchant_id == "M-001" and s.status == SettlementStatus.PENDING
            for s in result
        )

    def test_no_match_when_combination_absent(self, populated):
        """존재하는 판매자라도 해당 상태 레코드가 없으면 빈 리스트"""
        service, _ = populated

        result = service.list_settlements(
            merchant_id="M-001", status=SettlementStatus.FAILED
        )

        assert result == []

    def test_unknown_merchant_with_status(self, populated):
        service, _ = populated

        result = service.list_settlements(
            merchant_id="M-999", status=SettlementStatus.COMPLETED
        )

        assert result == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. process_settlement() — 없는 ID 조회 시 None 반환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestProcessSettlementNotFound:

    def test_returns_none_on_empty_store(self, service):
        assert service.process_settlement("STL-NOTEXIST") is None

    def test_returns_none_when_other_records_exist(self, service, june_period):
        """다른 레코드가 있어도 ID가 다르면 None, 기존 레코드는 그대로"""
        start, end = june_period
        existing = service.calculate_settlement("M-001", start, end)

        result = service.process_settlement("STL-NOTEXIST")

        assert result is None
        assert existing.status == SettlementStatus.PENDING  # 영향 없음
        assert existing.processed_at is None

    @pytest.mark.parametrize("bad_id", ["", "stl-unknown", "STL-00000000"])
    def test_returns_none_for_various_unknown_ids(self, service, bad_id):
        assert service.process_settlement(bad_id) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. calculate_settlement() — 빈 기간 (주문 0건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCalculateSettlementEmptyPeriod:

    def test_zero_orders_produces_zero_amounts(self, service, june_period):
        start, end = june_period

        record = service.calculate_settlement("M-001", start, end)

        assert record.order_count == 0
        assert record.total_sales == Decimal("0")
        assert record.total_fee == Decimal("0")
        assert record.net_amount == Decimal("0")

    def test_amounts_are_decimal_not_int(self, service, june_period):
        """빈 목록 sum()의 초기값 Decimal("0") 덕분에 int 0이 아닌 Decimal이어야 함"""
        start, end = june_period

        record = service.calculate_settlement("M-001", start, end)

        assert isinstance(record.total_sales, Decimal)
        assert isinstance(record.total_fee, Decimal)
        assert isinstance(record.net_amount, Decimal)

    def test_record_metadata_is_filled(self, service, june_period):
        start, end = june_period

        record = service.calculate_settlement("M-001", start, end)

        assert record.settlement_id.startswith("STL-")
        assert len(record.settlement_id) == len("STL-") + 8
        assert record.merchant_id == "M-001"
        assert record.period_start == start
        assert record.period_end == end
        assert record.status == SettlementStatus.PENDING

    def test_empty_record_is_still_stored(self, service, june_period):
        """0건이어도 정산 레코드는 저장소에 남아 조회 가능해야 함"""
        start, end = june_period

        record = service.calculate_settlement("M-001", start, end)

        assert service.list_settlements(merchant_id="M-001") == [record]

    def test_orders_exist_but_none_qualify(self, service, june_period):
        """
        저장소에 주문이 있어도 정산 조건을 하나라도 어기면 0건이어야 함

          - 기간 이전/이후 완료      (④ 기간 조건 위반)
          - 다른 판매자              (① 판매자 조건 위반)
          - 기간 내지만 PENDING      (② 상태 조건 위반)
          - 기간 내지만 CANCELLED    (② 상태 조건 위반)
        """
        start, end = june_period
        cases = [
            ("ORD-BEFORE", "M-001", OrderStatus.COMPLETED, datetime(2026, 5, 31, 23, 59, 59)),
            ("ORD-AFTER",  "M-001", OrderStatus.COMPLETED, datetime(2026, 7, 1, 0, 0, 0)),
            ("ORD-OTHER",  "M-002", OrderStatus.COMPLETED, datetime(2026, 6, 15, 12, 0, 0)),
            ("ORD-PEND",   "M-001", OrderStatus.PENDING,   datetime(2026, 6, 15, 12, 0, 0)),
            ("ORD-CANCEL", "M-001", OrderStatus.CANCELLED, datetime(2026, 6, 15, 12, 0, 0)),
        ]
        for order_id, merchant, status, completed_at in cases:
            service.add_order(Order(
                order_id=order_id,
                merchant_id=merchant,
                customer_id="C-001",
                amount=Decimal("100000"),
                status=status,
                completed_at=completed_at,
            ))

        record = service.calculate_settlement("M-001", start, end)

        assert len(service.get_orders()) == 5  # 주문은 실제로 저장돼 있음
        assert record.order_count == 0
        assert record.total_sales == Decimal("0")
        assert record.total_fee == Decimal("0")
        assert record.net_amount == Decimal("0")
