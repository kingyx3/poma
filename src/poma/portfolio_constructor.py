from __future__ import annotations

from collections import defaultdict

from poma.models import CombinedTargetPosition, StrategyTarget, StrategyTargetBook


def combine_strategy_target_books(
    books: list[StrategyTargetBook],
    portfolio_value_usd: float,
) -> tuple[list[CombinedTargetPosition], list[str]]:
    """Merge every strategy sleeve's targets into one portfolio-level target per ticker.

    Two sleeves can both want the same ticker; this collapses them into a single combined
    notional/weight while keeping each sleeve's contribution for attribution in reports. It also
    flags overlaps and a combined target notional that would exceed the account's total value.
    """
    warnings: list[str] = []
    contributions_by_ticker: dict[str, list[StrategyTarget]] = defaultdict(list)
    for book in books:
        for target in book.targets:
            contributions_by_ticker[target.ticker].append(target)

    combined: list[CombinedTargetPosition] = []
    for ticker in sorted(contributions_by_ticker):
        contributions = tuple(contributions_by_ticker[ticker])
        notional = sum(contribution.target_notional for contribution in contributions)
        weight = notional / portfolio_value_usd if portfolio_value_usd else 0.0
        combined.append(
            CombinedTargetPosition(
                ticker=ticker,
                target_weight=weight,
                target_notional=notional,
                contributions=contributions,
            )
        )
        strategy_names = sorted({contribution.strategy_name for contribution in contributions})
        if len(strategy_names) > 1:
            warnings.append(
                f"{ticker} target combines overlapping allocations from strategies: "
                f"{', '.join(strategy_names)}"
            )

    total_notional = sum(position.target_notional for position in combined)
    if portfolio_value_usd and total_notional > portfolio_value_usd + 1e-6:
        warnings.append(
            f"combined strategy targets (${total_notional:,.2f}) exceed portfolio value "
            f"(${portfolio_value_usd:,.2f}); block execution"
        )
    return combined, warnings
