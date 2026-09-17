CREATE TABLE app.CONTENT (
    CONTENT_ID INTEGER PRIMARY KEY,
    TITLE VARCHAR(240) NOT NULL,
    OWNER_USER_ID INTEGER NOT NULL
);

-- sqlctx_sample_metadata: {"actual": 1, "requested": 10, "shortage_reason": "fixture"}
-- sqlctx_sample_row: {"CONTENT_ID": 501, "OWNER_USER_ID": 1, "TITLE": "Example content"}
