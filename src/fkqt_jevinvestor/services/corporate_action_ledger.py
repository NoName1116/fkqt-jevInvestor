from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

from fkqt_jevinvestor.domain.corporate_actions import (
    CorporateActionEventV1,
    EntitlementLotV1,
    EntitlementV1,
)
from fkqt_jevinvestor.services.portfolio_service import (
    COST_QUANTUM,
    MONEY_QUANTUM,
    PortfolioLedger,
    PositionLot,
)


def freeze_entitlement(
    event: CorporateActionEventV1, ledger: PortfolioLedger, record_date: date,
) -> EntitlementV1:
    if record_date != event.record_date:
        raise ValueError("CORPORATE_ACTION_RECORD_DATE_MISMATCH")
    lots = [lot for lot in ledger.position(event.symbol).lots if lot.remaining_quantity > 0]
    if any(lot.acquired_on > record_date for lot in lots):
        raise ValueError("CORPORATE_ACTION_RECORD_HISTORY_MISSING")
    qty = sum(lot.remaining_quantity for lot in lots)
    with localcontext() as context:
        context.prec = 50
        bonus = Decimal(qty) * event.bonus_ratio
        if bonus != bonus.to_integral_value():
            raise ValueError("CORPORATE_ACTION_FRACTIONAL_UNSUPPORTED")
        exact = [Decimal(lot.remaining_quantity) * event.bonus_ratio for lot in lots]
        assigned = [int(number.to_integral_value(rounding=ROUND_DOWN)) for number in exact]
        remainder = int(bonus) - sum(assigned)
        ranking = sorted(range(len(lots)), key=lambda i: (-(exact[i] - assigned[i]), lots[i].acquired_on, i))
        for index in ranking[:remainder]:
            assigned[index] += 1
        cash = (qty * event.cash_per_share_gross).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
    return EntitlementV1(
        event_id=event.event_id, symbol=event.symbol, record_date=record_date,
        lot_allocations=tuple(EntitlementLotV1(
            acquired_on=lot.acquired_on, quantity=lot.remaining_quantity, cost=lot.total_cost,
            bonus_quantity=assigned[i],
        ) for i, lot in enumerate(lots)),
        entitled_quantity=qty, cash_receivable=cash, bonus_quantity=int(bonus),
    )


def apply_entitlement_stage(
    ledger: PortfolioLedger, entitlement: EntitlementV1, event: CorporateActionEventV1,
    *, stage: str, effective_date: date,
) -> EntitlementV1:
    if entitlement.event_id != event.event_id or entitlement.symbol != event.symbol:
        raise ValueError("CORPORATE_ACTION_EVENT_MISMATCH")
    if stage == "EX":
        if effective_date != event.ex_date:
            raise ValueError("CORPORATE_ACTION_STAGE_DATE_MISMATCH")
        if entitlement.ex_applied:
            return entitlement
        ledger.dividend_receivable += entitlement.cash_receivable
        ledger.dividend_income += entitlement.cash_receivable
        position = ledger.position(event.symbol)
        old_lots = list(position.lots)
        bonus_lots: list[PositionLot] = []
        bonus_by_date: dict[date, int] = {}
        for allocation in entitlement.lot_allocations:
            bonus_by_date[allocation.acquired_on] = (
                bonus_by_date.get(allocation.acquired_on, 0) + allocation.bonus_quantity
            )
        for acquired_on, bonus_quantity in sorted(bonus_by_date.items()):
            if bonus_quantity == 0:
                continue
            matched = [lot for lot in old_lots if lot.acquired_on == acquired_on and lot.remaining_quantity > 0]
            retained_qty = sum(lot.remaining_quantity for lot in matched)
            retained_cost = sum((lot.total_cost for lot in matched), Decimal(0))
            with localcontext() as context:
                context.prec = 50
                bonus_cost = (retained_cost * bonus_quantity / (retained_qty + bonus_quantity)).quantize(COST_QUANTUM, ROUND_HALF_UP)
                remaining = bonus_cost
                for index, lot in enumerate(matched):
                    share = remaining if index == len(matched) - 1 else (
                        (bonus_cost * lot.total_cost / retained_cost).quantize(COST_QUANTUM, ROUND_HALF_UP)
                        if retained_cost else Decimal(0)
                    )
                    lot.total_cost -= share
                    remaining -= share
            bonus_lots.append(PositionLot(
                acquired_on=acquired_on, remaining_quantity=bonus_quantity,
                total_cost=bonus_cost, available_on=event.share_list_date,
            ))
        position.lots.extend(bonus_lots)
        return entitlement.model_copy(update={"ex_applied": True})
    if not entitlement.ex_applied:
        raise ValueError("CORPORATE_ACTION_EX_STAGE_REQUIRED")
    if stage == "PAY":
        if event.pay_date is None or effective_date != event.pay_date:
            raise ValueError("CORPORATE_ACTION_STAGE_DATE_MISMATCH")
        if not entitlement.paid:
            if ledger.dividend_receivable < entitlement.cash_receivable:
                raise ValueError("CORPORATE_ACTION_RECEIVABLE_INCONSISTENT")
            ledger.dividend_receivable -= entitlement.cash_receivable
            ledger.cash_balance += entitlement.cash_receivable
        return entitlement.model_copy(update={"paid": True})
    if stage == "LIST":
        if event.share_list_date is None or effective_date != event.share_list_date:
            raise ValueError("CORPORATE_ACTION_STAGE_DATE_MISMATCH")
        return entitlement.model_copy(update={"shares_listed": True})
    raise ValueError("CORPORATE_ACTION_STAGE_UNSUPPORTED")
