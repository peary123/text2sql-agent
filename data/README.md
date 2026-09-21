# data/

This directory is gitignored — the Spider dataset is ~100 MB and is not ours to redistribute.

To populate it:

    python scripts/00_prepare_data.py

That downloads `spider.zip` (95 MB) from the official Yale release and extracts it so
that the following paths exist:

    data/spider/dev.json
    data/spider/train_spider.json
    data/spider/tables.json
    data/spider/database/<db_id>/<db_id>.sqlite

Source: https://yale-lily.github.io/spider (Spider 1.0, CC BY-SA 4.0)
