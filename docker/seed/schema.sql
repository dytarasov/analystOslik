-- Реальный набор данных EdTech-аналитики, импортируется из data.zip.
-- Два слоя: dict (справочники), cdm (фактовая витрина по дням).
-- Поля без явной семантики оставлены String — на семантический слой
-- их разметит профайлер при участии админа.

CREATE DATABASE IF NOT EXISTS dict;
CREATE DATABASE IF NOT EXISTS cdm;

DROP TABLE IF EXISTS dict.schools;
CREATE TABLE dict.schools
(
    school_id              UInt64,
    school_number          String,
    official_title         String,
    official_title_short   String,
    region                 Nullable(UInt32),
    country                Nullable(UInt32),
    school_type            Nullable(UInt8),
    merged_to_school_id    Nullable(UInt64),
    manager_id             Nullable(UInt64),
    approved               UInt8 DEFAULT 0,
    students_count         Nullable(UInt32),
    payment_type           Nullable(UInt8),
    municipality_id        Nullable(UInt32),
    mun_name               String,
    fias_name              String,
    fias_guid              String,
    mun_level              String,
    mun_type               String,
    parent_mun_name        String,
    parent_mun_type        String,
    created_at             Nullable(DateTime),
    updated_at             Nullable(DateTime),
    deleted_at             Nullable(DateTime),
    ptn_date               Date,
    ch_created_at          DateTime
)
ENGINE = MergeTree
ORDER BY school_id
SETTINGS index_granularity = 8192;

DROP TABLE IF EXISTS dict.teachers;
CREATE TABLE dict.teachers
(
    teacher_id             UInt64,
    is_headmaster          UInt8 DEFAULT 0,
    verified_headmaster    Nullable(String),
    email                  String,
    name                   String,
    last_name              String,
    middle_name            String,
    raw_name               String,
    raw_middle_name        String,
    raw_last_name          String,
    lead_subjects          String,
    phone                  String,
    raw_phone              String,
    region_id              Nullable(UInt32),
    school_id              Nullable(UInt64),
    first_grade            Nullable(UInt8),
    boarding_state         String,
    payment_type           Nullable(String),
    last_activity_date     Nullable(DateTime),
    consent_use_data       UInt8 DEFAULT 0,
    consent_mailing        UInt8 DEFAULT 0,
    consent_calling        UInt8 DEFAULT 0,
    segment                String,
    updated_at             Nullable(DateTime),
    created_at             Nullable(DateTime),
    deleted_at             Nullable(DateTime),
    deleted                UInt8 DEFAULT 0,
    account_id             String,
    uuid                   String,
    ptn_date               Date,
    lead_subjects_array    String,
    ch_created_at          DateTime
)
ENGINE = MergeTree
ORDER BY teacher_id
SETTINGS index_granularity = 8192;

DROP TABLE IF EXISTS cdm.teachers_events_daily;
CREATE TABLE cdm.teachers_events_daily
(
    teacher_id                    UInt64,
    ptn_date                      Date,
    tch_created_at                Nullable(Date),
    group_parallel                String,
    parallel_array                String,
    school_id                     Nullable(UInt64),
    school                        String,
    region_id                     Nullable(UInt32),
    reg_name                      String,
    region_type                   String,
    teacher_authorization_flag    UInt8 DEFAULT 0,
    desktop_flag                  UInt8 DEFAULT 0,
    mobile_web_flag               UInt8 DEFAULT 0,
    mobile_app_flag               UInt8 DEFAULT 0,
    app_android_flag              UInt8 DEFAULT 0,
    app_ios_flag                  UInt8 DEFAULT 0,
    app_first_dt                  Nullable(Date),
    registered_students_count     UInt32 DEFAULT 0,
    registered_student_ids        String,
    registered_student_ids_0      String,
    registered_student_ids_1_4    String,
    registered_student_ids_5_8    String,
    registered_student_ids_5_9    String,
    registered_student_ids_10_11  String,
    added_student_ids             String,
    added_student_ids_0           String,
    added_student_ids_1_4         String,
    added_student_ids_5_8         String,
    added_student_ids_5_9         String,
    added_student_ids_10_11       String,
    activated_students_count      UInt32 DEFAULT 0,
    activated_student_ids         String,
    first_session_students_count  UInt32 DEFAULT 0,
    first_session_student_ids     String,
    active_student_ids            String,
    active_student_ids_0          String,
    active_student_ids_1_4        String,
    active_student_ids_5_8        String,
    active_student_ids_5_9        String,
    active_student_ids_10_11      String,
    trial                         UInt32 DEFAULT 0,
    trial_0                       UInt32 DEFAULT 0,
    trial_1_4                     UInt32 DEFAULT 0,
    trial_5_8                     UInt32 DEFAULT 0,
    trial_5_9                     UInt32 DEFAULT 0,
    trial_10_11                   UInt32 DEFAULT 0,
    trial_ids                     String,
    trial_ids_0                   String,
    trial_ids_1_4                 String,
    trial_ids_5_8                 String,
    trial_ids_5_9                 String,
    trial_ids_10_11               String,
    amount_sum                    Float64 DEFAULT 0,
    amount_sum_0                  Float64 DEFAULT 0,
    amount_sum_1_4                Float64 DEFAULT 0,
    amount_sum_5_8                Float64 DEFAULT 0,
    amount_sum_5_9                Float64 DEFAULT 0,
    amount_sum_10_11              Float64 DEFAULT 0,
    payed_student_ids             String,
    payed_student_ids_0           String,
    payed_student_ids_1_4         String,
    payed_student_ids_5_8         String,
    payed_student_ids_5_9         String,
    payed_student_ids_10_11       String,
    start_lessons_count           UInt32 DEFAULT 0,
    b2t_lessons_count             UInt32 DEFAULT 0,
    homeworks_count               UInt32 DEFAULT 0,
    autodomashka_count            UInt32 DEFAULT 0,
    only_card_count               UInt32 DEFAULT 0,
    hw_count_0                    UInt32 DEFAULT 0,
    hw_count_1_4                  UInt32 DEFAULT 0,
    hw_count_5_8                  UInt32 DEFAULT 0,
    hw_count_5_9                  UInt32 DEFAULT 0,
    hw_count_9_11                 UInt32 DEFAULT 0,
    hw_count_10_11                UInt32 DEFAULT 0,
    hw_count_app                  UInt32 DEFAULT 0,
    day                           Date,
    month                         String,
    year                          String,
    ch_created_at                 DateTime
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ptn_date)
ORDER BY (ptn_date, teacher_id)
SETTINGS index_granularity = 8192;
