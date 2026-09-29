-- user_version 2
CREATE TABLE genres (
	id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE invite_codes (
	id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	note VARCHAR(200) NOT NULL, 
	max_uses INTEGER, 
	uses INTEGER NOT NULL, 
	expires_at DATETIME, 
	created_by INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE item_genres (
	item_id INTEGER NOT NULL, 
	genre_id INTEGER NOT NULL, 
	PRIMARY KEY (item_id, genre_id), 
	FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, 
	FOREIGN KEY(genre_id) REFERENCES genres (id) ON DELETE CASCADE
);

CREATE TABLE item_vibes (
	item_id INTEGER NOT NULL, 
	vibe_id INTEGER NOT NULL, 
	PRIMARY KEY (item_id, vibe_id), 
	FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, 
	FOREIGN KEY(vibe_id) REFERENCES vibes (id) ON DELETE CASCADE
);

CREATE TABLE items (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	creator VARCHAR(500) NOT NULL, 
	year INTEGER, 
	cover_file VARCHAR(255), 
	spotify_url VARCHAR(500), 
	external_id VARCHAR(64), 
	notes TEXT, 
	genres_checked BOOLEAN DEFAULT 0 NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE vibes (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	color VARCHAR(7) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, name), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_items_kind ON items (kind);

CREATE INDEX ix_items_user_id ON items (user_id);

CREATE INDEX ix_items_year ON items (year);

CREATE INDEX ix_vibes_user_id ON vibes (user_id);

CREATE UNIQUE INDEX ux_users_username_lower ON users (lower(username));

