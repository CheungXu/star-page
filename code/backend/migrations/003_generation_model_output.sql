ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS model_output_text text;
