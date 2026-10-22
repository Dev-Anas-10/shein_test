CREATE TABLE IF NOT EXISTS users(
  id SERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS scrapes(
  id UUID PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  data JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cart_items(
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goods_id TEXT NOT NULL,
  goods_sn TEXT DEFAULT '',
  name TEXT NOT NULL,
  image TEXT DEFAULT '',
  original_price NUMERIC(12,2) NOT NULL DEFAULT 0,
  sale_price NUMERIC(12,2) NOT NULL DEFAULT 0,
  currency TEXT DEFAULT 'SAR',
  selected_variant TEXT DEFAULT '',
  sku_code TEXT NOT NULL DEFAULT '',
  qty INT NOT NULL DEFAULT 1,
  added_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, goods_id, sku_code)
);
