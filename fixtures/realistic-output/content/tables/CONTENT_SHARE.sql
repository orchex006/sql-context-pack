CREATE TABLE app.CONTENT_SHARE (
    CONTENT_ID INTEGER NOT NULL,
    USER_ID INTEGER NOT NULL,
    PRIMARY KEY (CONTENT_ID, USER_ID)
);

-- sqlctx_sample_metadata: {"actual": 1, "requested": 10, "shortage_reason": "fixture"}
-- sqlctx_sample_row: {"CONTENT_ID": 501, "USER_ID": 1}
