from procurement.models import VendorBid, NormalizedBid, NormalizationAdjustment


def normalize_bid(bid: VendorBid, target_currency: str, fx_rates: dict[str, float]) -> NormalizedBid:
    if bid.extraction_status == "failed":
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, extraction_status="failed")

    adjustments: list[NormalizationAdjustment] = []
    rate = 1.0 if (not bid.currency or bid.currency == target_currency) else fx_rates.get(bid.currency, 1.0)

    base = bid.base_price
    converted = base * rate
    if rate != 1.0:
        adjustments.append(NormalizationAdjustment(
            kind="currency", description=f"{bid.currency}->{target_currency} @ {rate}",
            from_value=base, to_value=converted, delta=converted - base))

    total = converted
    if bid.vat_included and bid.vat_rate:
        ex = total / (1.0 + bid.vat_rate)
        adjustments.append(NormalizationAdjustment(
            kind="vat", description=f"remove VAT {bid.vat_rate}",
            from_value=total, to_value=ex, delta=ex - total))
        total = ex

    if not bid.freight_included and bid.freight_amount:
        freight = bid.freight_amount * rate
        newtotal = total + freight
        adjustments.append(NormalizationAdjustment(
            kind="freight", description="add freight to reach freight-included basis",
            from_value=total, to_value=newtotal, delta=freight))
        total = newtotal

    if bid.discount_pct:
        discount = total * bid.discount_pct
        newtotal = total - discount
        adjustments.append(NormalizationAdjustment(
            kind="discount", description=f"apply discount {bid.discount_pct}",
            from_value=total, to_value=newtotal, delta=-discount))
        total = newtotal

    return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                         normalized_total=round(total, 2), adjustments=adjustments)
