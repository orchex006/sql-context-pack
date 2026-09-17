SET ANSI_NULLS ON;
GO
SET QUOTED_IDENTIFIER ON;
GO
SET XACT_ABORT ON;
GO

/*
    เก็บดัชนีบริบทของ TABLE, PROCEDURE และ FUNCTION สำหรับ SQL Context Pack
    โดยไม่เก็บ SQL body หรือข้อมูล credential
*/

IF SCHEMA_ID(N'agrimap_app') IS NULL
    BEGIN
        EXEC(N'CREATE SCHEMA [agrimap_app] AUTHORIZATION [dbo];');
    END;
GO

IF OBJECT_ID(N'[agrimap_app].[DB_METADATA_CONTEXT]', N'U') IS NULL
    BEGIN
        BEGIN TRY
            BEGIN TRANSACTION;

            CREATE TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            (
            -- Primary Key
                [ID] NUMERIC(38, 0) IDENTITY(1, 1) NOT NULL,

                -- Public and Object Identity
                [DB_METADATA_CONTEXT_ID] UNIQUEIDENTIFIER NOT NULL,
                [SCHEMA_NAME] NVARCHAR(128) NOT NULL,
                [OBJECT_NAME] NVARCHAR(256) NOT NULL,
                [OBJECT_TYPE] VARCHAR(20) NOT NULL,

                -- Confirmed Context
                [CONTEXT_CODE] VARCHAR(64) NULL,
                [DESCRIPTION] NVARCHAR(2000) NULL,
                [TAGS_JSON] NVARCHAR(MAX) NOT NULL,
                [CLASSIFICATION_STATUS] VARCHAR(20) NOT NULL,
                [CLASSIFICATION_SOURCE] VARCHAR(20) NOT NULL,

                -- Integrity and Generation
                [SOURCE_FINGERPRINT] VARCHAR(71) NULL,
                [CONTENT_HASH] VARCHAR(71) NULL,
                [MANAGED_RELATIVE_PATH] NVARCHAR(1000) NULL,
                [HEADER_VERSION] INT NOT NULL,
                [OUTPUT_FORMAT_VERSION] INT NOT NULL,
                [EVIDENCE_JSON] NVARCHAR(MAX) NOT NULL,
                [LAST_CLASSIFIED_AT] DATETIME2(7) NULL,
                [LAST_GENERATED_AT] DATETIME2(7) NULL,

                -- Audit Columns
                [DATE_CREATED] DATETIME2(7) NOT NULL,
                [DATE_MODIFIED] DATETIME2(7) NULL,
                [USER_CREATED] NUMERIC(38, 0) NOT NULL,
                [USER_MODIFIED] NUMERIC(38, 0) NULL,
                [DEL_FLAG] BIT NOT NULL,

                CONSTRAINT [PK_DB_METADATA_CONTEXT] PRIMARY KEY CLUSTERED (
                    [ID] ASC
                )
            ) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_PUBLIC_ID]
            DEFAULT (NEWSEQUENTIALID()) FOR [DB_METADATA_CONTEXT_ID];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_TAGS]
            DEFAULT (N'[]') FOR [TAGS_JSON];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_STATUS]
            DEFAULT ('UNRESOLVED') FOR [CLASSIFICATION_STATUS];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_SOURCE]
            DEFAULT ('UNKNOWN') FOR [CLASSIFICATION_SOURCE];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_HEADER_VERSION]
            DEFAULT (1) FOR [HEADER_VERSION];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_OUTPUT_VERSION]
            DEFAULT (2) FOR [OUTPUT_FORMAT_VERSION];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_EVIDENCE]
            DEFAULT (N'[]') FOR [EVIDENCE_JSON];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_DATE_CREATED]
            DEFAULT (SYSUTCDATETIME()) FOR [DATE_CREATED];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [DF_DB_METADATA_CONTEXT_DEL_FLAG]
            DEFAULT (0) FOR [DEL_FLAG];

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_OBJECT_TYPE]
            CHECK ([OBJECT_TYPE] IN ('TABLE', 'PROCEDURE', 'FUNCTION'));

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_STATUS]
            CHECK ([CLASSIFICATION_STATUS] IN ('CONFIRMED', 'UNRESOLVED'));

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_SOURCE]
            CHECK ([CLASSIFICATION_SOURCE] IN ('OWNER', 'RULE', 'UNKNOWN'));

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_CONTEXT_CODE]
            CHECK
            (
                [CONTEXT_CODE] IS NULL
                OR
                (
                    LEN([CONTEXT_CODE]) BETWEEN 1 AND 64
                    AND [CONTEXT_CODE] COLLATE Latin1_General_100_BIN2
                    NOT LIKE '%[^a-z0-9_-]%'
                )
            );

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_CLASSIFICATION]
            CHECK
            (
                (
                    [CLASSIFICATION_STATUS] = 'CONFIRMED'
                    AND [CONTEXT_CODE] IS NOT NULL
                    AND [CLASSIFICATION_SOURCE] IN ('OWNER', 'RULE')
                )
                OR
                (
                    [CLASSIFICATION_STATUS] = 'UNRESOLVED'
                    AND [CONTEXT_CODE] IS NULL
                    AND [CLASSIFICATION_SOURCE] = 'UNKNOWN'
                    AND [TAGS_JSON] = N'[]'
                )
            );

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_TAGS_JSON]
            CHECK (
                ISJSON([TAGS_JSON]) = 1 AND LEFT(LTRIM([TAGS_JSON]), 1) = N'['
            );

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_EVIDENCE_JSON]
            CHECK (
                ISJSON([EVIDENCE_JSON]) = 1
                AND LEFT(LTRIM([EVIDENCE_JSON]), 1) = N'['
            );

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_HEADER_VERSION]
            CHECK ([HEADER_VERSION] = 1);

            ALTER TABLE [agrimap_app].[DB_METADATA_CONTEXT]
            ADD CONSTRAINT [CK_DB_METADATA_CONTEXT_OUTPUT_VERSION]
            CHECK ([OUTPUT_FORMAT_VERSION] = 2);

            CREATE UNIQUE NONCLUSTERED INDEX
            [UX_DB_METADATA_CONTEXT_OBJECT_ACTIVE]
                ON [agrimap_app].[DB_METADATA_CONTEXT]
                (
                    [SCHEMA_NAME] ASC,
                    [OBJECT_TYPE] ASC,
                    [OBJECT_NAME] ASC
                )
                WHERE [DEL_FLAG] = 0
                ON [PRIMARY];

            CREATE NONCLUSTERED INDEX [IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE]
                ON [agrimap_app].[DB_METADATA_CONTEXT]
                (
                    [CONTEXT_CODE] ASC,
                    [CLASSIFICATION_STATUS] ASC,
                    [OBJECT_TYPE] ASC
                )
                INCLUDE
                (
                    [SCHEMA_NAME],
                    [OBJECT_NAME],
                    [DESCRIPTION],
                    [TAGS_JSON],
                    [CONTENT_HASH],
                    [MANAGED_RELATIVE_PATH]
                )
                WHERE [DEL_FLAG] = 0
                ON [PRIMARY];

            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value
                = N'ดัชนีบริบทของออบเจ็กต์ฐานข้อมูลสำหรับสร้าง SQL Context',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT';

            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รหัสลำดับภายใน',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'ID';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รหัสสาธารณะของรายการบริบท',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'DB_METADATA_CONTEXT_ID';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'ชื่อสคีมาของออบเจ็กต์ต้นทาง',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'SCHEMA_NAME';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'ชื่อออบเจ็กต์ต้นทาง',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'OBJECT_NAME';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'ประเภท TABLE, PROCEDURE หรือ FUNCTION',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'OBJECT_TYPE';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รหัสบริบทหลักที่ยืนยันแล้ว',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'CONTEXT_CODE';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'คำอธิบายบริบทจากหลักฐานหรือเจ้าของระบบ',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'DESCRIPTION';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รายการแท็กที่ยืนยันแล้วในรูปแบบ JSON array',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'TAGS_JSON';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'สถานะ CONFIRMED หรือ UNRESOLVED',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'CLASSIFICATION_STATUS';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'แหล่งการยืนยัน OWNER, RULE หรือ UNKNOWN',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'CLASSIFICATION_SOURCE';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'ลายนิ้วมือของนิยามต้นทาง',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'SOURCE_FINGERPRINT';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'ค่า SHA-256 ของเนื้อหา SQL ที่จัดการแล้ว',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'CONTENT_HASH';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'พาธสัมพัทธ์ภายใต้รากที่เจ้าของลงทะเบียน',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'MANAGED_RELATIVE_PATH';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'เวอร์ชันของ managed SQL header',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'HEADER_VERSION';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'เวอร์ชันของรูปแบบผลลัพธ์ SQL Context',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'OUTPUT_FORMAT_VERSION';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'หลักฐานที่ปลอดภัยในรูปแบบ JSON array',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'EVIDENCE_JSON';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'วันเวลาล่าสุดที่จำแนกบริบทเป็น UTC',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'LAST_CLASSIFIED_AT';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'วันเวลาล่าสุดที่นำรายการไปสร้าง Context เป็น UTC',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'LAST_GENERATED_AT';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'วันเวลาที่สร้างรายการเป็น UTC',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'DATE_CREATED';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'วันเวลาที่แก้ไขรายการเป็น UTC',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'DATE_MODIFIED';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รหัสผู้ใช้งานที่สร้างรายการ',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'USER_CREATED';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'รหัสผู้ใช้งานที่แก้ไขรายการ',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'USER_MODIFIED';
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = N'สถานะการลบแบบอ่อน 0 ใช้งาน และ 1 ลบแล้ว',
                @level0type = N'SCHEMA',
                @level0name = N'agrimap_app',
                @level1type = N'TABLE',
                @level1name = N'DB_METADATA_CONTEXT',
                @level2type = N'COLUMN',
                @level2name = N'DEL_FLAG';

            COMMIT TRANSACTION;
        END TRY
        BEGIN CATCH
            IF @@TRANCOUNT > 0
                ROLLBACK TRANSACTION;

            THROW;
        END CATCH;
    END;
GO
