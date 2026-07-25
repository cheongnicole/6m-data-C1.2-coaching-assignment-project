--Parsing categories column (JSON array) using DBgate, using metadata_jobPostId as unique job ID
WITH categoriestable AS (SELECT metadata_jobPostId, postedCompany_name, title, categories FROM SGJobData WHERE metadata_jobPostId NOT NULL)
SELECT
    metadata_jobPostId,
    postedCompany_name,
    title,
    parsed_records.id,
    parsed_records.category
FROM categoriestable,
UNNEST(
    CAST(categories AS STRUCT(id INT, category VARCHAR)[])
) AS t(parsed_records)
ORDER BY metadata_jobPostId ASC