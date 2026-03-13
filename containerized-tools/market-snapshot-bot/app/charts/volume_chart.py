from pathlib import Path

import matplotlib.pyplot as plt


def generate_volume_chart(
    payload: dict[str, object],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))

    for symbol_data in payload["symbols"]:
        symbol = symbol_data["symbol"]
        samples = symbol_data["samples"]

        times = [
            sample.get("timestamp_label_et", str(sample["timestamp_et"]))
            for sample in samples
        ]
        volumes = [int(sample["volume"]) for sample in samples]

        plt.plot(times, volumes, marker="o", label=symbol)

    plt.title("Intraday Volume Window")
    plt.xlabel("Timestamp (ET)")
    plt.ylabel("Volume")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
