-- Increment day spent and hourly spent only when the day budget key exists.
-- KEYS[1] = day budget key
-- KEYS[2] = hour key (llm_budget:hour:<day>:<HH>)
-- ARGV[1] = billable_tokens (int >= 0)
-- ARGV[2] = hour EXPIREAT unix seconds (end of hour + 1h)
-- ARGV[3] = spike threshold (floor(share * budget))
-- Returns: {added, crossed, hour_tokens}
--   added = 1 if day key existed and counters were incremented, else 0
--   crossed = 1 if this add crossed the hourly threshold (prev < T <= new)
--   hour_tokens = hourly counter after this add (0 when not added)
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {0, 0, 0}
end
local delta = tonumber(ARGV[1])
local hour_expire = tonumber(ARGV[2])
local threshold = tonumber(ARGV[3])
local prev_raw = redis.call('GET', KEYS[2])
local prev = 0
if prev_raw then
  prev = tonumber(prev_raw)
end
redis.call('INCRBY', KEYS[1], delta)
local hour_tokens = redis.call('INCRBY', KEYS[2], delta)
redis.call('EXPIREAT', KEYS[2], hour_expire)
local crossed = 0
if prev < threshold and hour_tokens >= threshold then
  crossed = 1
end
return {1, crossed, hour_tokens}
