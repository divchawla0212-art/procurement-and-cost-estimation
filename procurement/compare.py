from procurement.models import VendorBid, NormalizedBid, ComparisonRow, ComparisonTable


def build_comparison(bids: list[VendorBid], normalized: list[NormalizedBid],
                     target_currency: str) -> ComparisonTable:
    norm_by_vendor = {n.vendor: n for n in normalized}
    rows: list[ComparisonRow] = []
    for bid in bids:
        n = norm_by_vendor.get(bid.vendor)
        rows.append(ComparisonRow(
            vendor=bid.vendor,
            currency=bid.currency,
            raw_base_price=bid.base_price if bid.extraction_status == "ok" else None,
            normalized_total=n.normalized_total if n else None,
            delivery_terms=bid.delivery_terms,
            delivery_time=bid.delivery_time,
            payment_terms=bid.payment_terms,
            engine_make=bid.engine_make,
            extraction_status=bid.extraction_status,
        ))
    rows.sort(key=lambda r: (r.normalized_total is None, r.normalized_total or 0.0))
    return ComparisonTable(target_currency=target_currency, rows=rows)
