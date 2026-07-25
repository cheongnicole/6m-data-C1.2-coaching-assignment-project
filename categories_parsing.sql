--Parsing categories column (JSON array) using DBgate, with metadata_jobPostId as unique job ID
WITH categoriestable AS (SELECT metadata_jobPostId, postedCompany_name, title, categories FROM SGJobData WHERE metadata_jobPostId NOT NULL)
SELECT
    metadata_jobPostId,
    postedCompany_name,
    title,
    parsed_categories.id,
    parsed_categories.category
FROM categoriestable,
UNNEST(
    CAST(categories AS STRUCT(id INT, category VARCHAR)[])
) AS t(parsed_categories)
ORDER BY metadata_jobPostId ASC
