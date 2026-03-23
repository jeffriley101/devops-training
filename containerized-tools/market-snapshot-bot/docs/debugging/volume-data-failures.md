### Debugging and Troubleshooting

#### Debugging Highlight - QQQ Volume Anomalies

During the transition from mock data to real Yahoo Finance minute-volume data, the volume workflow began showing unexpected zero-minute samples and distorted peaks in the generated charts.

##### Symptom

The intraday volume chart occasionally displayed:

- zero or near-zero values for individual one-minute intervals
- unusually large spikes in nearby minutes
- chart behavior that initially looked like a recording or charting bug

##### Investigation

I approached the issue as a data-pipeline debugging problem rather than assuming the chart code was wrong.

Questions tested during troubleshooting:

- Was Yahoo returning bad or irregular one-minute samples?
- Was the scheduled job actually running on time?
- Was the application recording returned data correctly?
- Was chart generation reading the stored data correctly?
- Was the chart itself misrepresenting valid input?

I first checked project history to identify where the real volume path entered the system:

```bash
git log --oneline --decorate -- containerized-tools/market-snapshot-bot/app/clients/market_data.py
```

That showed the real Yahoo Finance QQQ workflow was introduced in commit `efa17c4`:

```text
efa17c4 Add real Yahoo Finance price and QQQ volume workflows
```

This narrowed the likely problem area to the real data path rather than later chart-presentation changes.

I then reproduced the issue directly against Yahoo Finance outside the charting workflow using `yfinance` and confirmed that zero-volume one-minute rows were present in the upstream data itself.

##### Example validation

```python
import yfinance as yf

ticker = yf.Ticker("QQQ")
df = ticker.history(period="1d", interval="1m", auto_adjust=False, prepost=False)
print(df[df["Volume"] == 0][["Volume"]].head(20))
```

This confirmed that the anomaly was not being introduced only by chart code. The upstream minute data itself included zero-volume rows for isolated minutes.

##### Working theory

The observed pattern suggested that some one-minute intervals may be noisy or inconsistently represented in the Yahoo minute feed. In several cases, the minute preceding a zero showed an unusually large spike, raising the possibility that volume may be clustering across adjacent intervals in the returned dataset.

##### Response

Rather than hiding the anomaly, I treated it as an operational data-quality issue and adjusted the project direction accordingly:

- preserved the raw one-minute ingestion path for investigation
- continued gathering more historical examples before hard-coding a normalization rule
- evaluated whether slightly wider aggregation windows would produce more stable operator-facing charts
- considered two-minute aggregation after noticing the zero-minute pattern did not appear in consecutive runs

##### What this demonstrates

This debugging exercise shows how I approach production-style troubleshooting:

- isolate the symptom
- trace the change that introduced the new behavior
- validate the upstream dependency independently
- avoid blaming presentation code before proving the source
- hold off on overfitting a fix until enough evidence exists
