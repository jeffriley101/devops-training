INSERT INTO symbols (symbol, display_name, asset_type)
VALUES ('GC', 'Gold Futures', 'futures')
ON CONFLICT (symbol) DO NOTHING;
