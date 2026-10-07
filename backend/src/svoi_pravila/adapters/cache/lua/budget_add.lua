-- Increment spent tokens only when the budget key already exists.
-- KEYS[1] = budget key
-- ARGV[1] = billable_tokens (int >= 0)
-- Returns: 1 if incremented, 0 if key missing
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
redis.call('INCRBY', KEYS[1], tonumber(ARGV[1]))
return 1
