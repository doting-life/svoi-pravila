-- Atomic daily quota reserve: INCR only while below limit; EXPIREAT on first write.
-- KEYS[1] = counter key
-- KEYS[2] = reservation marker key
-- ARGV[1] = limit (int)
-- ARGV[2] = expire_at unix seconds
-- ARGV[3] = reservation_id (unused; marker key already encodes it)
-- Returns: {allowed(0|1), remaining_or_count, expire_at}
local limit = tonumber(ARGV[1])
local expire_at = tonumber(ARGV[2])
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= limit then
  return {0, current, expire_at}
end
local n = redis.call('INCR', KEYS[1])
if n == 1 then
  redis.call('EXPIREAT', KEYS[1], expire_at)
end
if n > limit then
  redis.call('DECR', KEYS[1])
  return {0, limit, expire_at}
end
redis.call('SET', KEYS[2], '1', 'NX', 'EXAT', expire_at)
return {1, limit - n, expire_at}
