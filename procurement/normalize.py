from procurement.models import VendorBid, NormalizedBid, NormalizationAdjustment


def _usable_rate(currency: str, target_currency: str,
                 fx_rates: dict[str, float]) -> float | None:
    """The multiplier to reach the target currency, or None if there isn't one.

    None means "refuse", never "assume". A non-positive configured rate counts
    as absent: 0.0 would convert the bid to nothing and sort it first, which is
    the defect this function exists to stop, not a rate.
    """
    if not currency or currency == target_currency:
        return 1.0
    rate = fx_rates.get(currency)
    if rate is None or rate <= 0:
        return None
    return rate


def normalize_bid(bid: VendorBid, target_currency: str, fx_rates: dict[str, float]) -> NormalizedBid:
    if bid.extraction_status == "failed":
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, extraction_status="failed")

    rate = _usable_rate(bid.currency, target_currency, fx_rates)
    if rate is None:
        # Early return, before VAT/freight/discount: every one of those steps
        # operates on a converted figure.
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, normalization_status="no_fx_rate")

    adjustments: list[NormalizationAdjustment] = []
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
