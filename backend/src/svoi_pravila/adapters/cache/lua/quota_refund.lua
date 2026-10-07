-- Idempotent refund: never below 0; no-op if reservation already refunded or unknown.
-- KEYS[1] = counter key
-- KEYS[2] = reservation marker key
-- Returns: 1 if refunded now, 0 if no-op
local marker = redis.call('GET', KEYS[2])
if marker ~= '1' then
  return 0
end
redis.call('SET', KEYS[2], '0', 'KEEPTTL')
local n = tonumber(redis.call('GET', KEYS[1]) or '0')
if n > 0 then
  redis.call('DECR', KEYS[1])
end
return 1
