# Strategy contract

Production behavior:

- Use the largest 500 US-listed companies by current market cap from the configured provider.
- Normalize provider rows into the internal snapshot contract.
- Collapse multiple share classes of the same company before ranking.
- Compute a dual score from current size and 90-day rank-rising velocity.
- Select the top 100 company stocks by that combined score.
- Equal-weight the selected names, subject to the configured risk caps.

This is a US top-market-cap strategy, not a strict index-constituent strategy.
