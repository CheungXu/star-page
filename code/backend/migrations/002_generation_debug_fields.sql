ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS user_prompt text,
  ADD COLUMN IF NOT EXISTS input_file_names jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS extracted_file_text text,
  ADD COLUMN IF NOT EXISTS compression_prompt text,
  ADD COLUMN IF NOT EXISTS model_prompt text,
  ADD COLUMN IF NOT EXISTS output_html_storage_key varchar(512);

UPDATE generation_tasks
SET
  user_prompt = COALESCE(user_prompt, prompt),
  model_prompt = COALESCE(model_prompt, prompt)
WHERE user_prompt IS NULL OR model_prompt IS NULL;
