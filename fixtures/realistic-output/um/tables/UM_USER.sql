CREATE TABLE app.UM_USER (
    USER_ID INTEGER PRIMARY KEY,
    USERNAME VARCHAR(120) NOT NULL,
    ROLE_ID INTEGER NOT NULL
);

-- sqlctx_sample_metadata: {"actual": 1, "requested": 10, "shortage_reason": "fixture"}
-- sqlctx_sample_row: {"ROLE_ID": 10, "USER_ID": 1, "USERNAME": "user_8F2M5S3B0K"}
