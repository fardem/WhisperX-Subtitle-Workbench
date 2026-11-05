-- probe_subtitles.lua  (WX_Probe_Untertitel.lua v2, kept for checking new Resolve versions)
-- Checks whether a script can put an SRT onto a specific subtitle track in DaVinci Resolve.
-- The plugin (resolve/WhisperX_Subtitles.lua) relies on the results below - run this probe
-- again after a Resolve update before trusting the plugin with an important project.
--
-- Place:  %APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Edit\
-- Start:  Workspace -> Scripts -> probe_subtitles
-- Output: Workspace -> Console (tab "Lua")
--
-- ONE variant per run (set VARIANT below). The project is saved before the attempt.
--
-- Results with Resolve Studio 21.1.2.10 on Windows:
--   - MediaPool:ImportMedia({srtPath}) returns a MediaPoolItem of type "Subtitle".
--   - AppendToTimeline({item}) puts the SRT on the ACTIVE subtitle track and appends it after
--     the last subtitle there. On an empty track it starts at the timeline start, the SRT
--     times stay relative (subtitle at 00:00:01,000 -> frame 30 at 29.97 fps).
--   - trackIndex and recordFrame in the clipInfo are ignored for subtitles (no crash).
--   - Only one subtitle track is active; AddTrack("subtitle") creates inactive tracks.
--   - Nothing is placed if the target track is not active.
--   - Working way (variant 7): deactivate all other subtitle tracks, activate the target, append.
--   - SetTrackName("subtitle", i, name) works.
--   - Count clips with #GetItemListInTrack("subtitle", i); pairs() reports 1 on empty tracks.

local VARIANT = 7
--   7 = ACTIVATE the target track (first empty / new), deactivate all others, then like 1
--   1 = AppendToTimeline({item})                          -> tested: lands on the active track
--   2 = {mediaPoolItem, recordFrame}                      -> tested: recordFrame is ignored
--   6 = insert a new track at position 1                  -> tested: lands on the active track
--   3 = {mediaPoolItem, recordFrame, trackIndex=target}   -> track selectable? (crash risk)
--   4 = like 2, all other subtitle tracks LOCKED
--   5 = like 2, all other subtitle tracks DISABLED

local REC_OFFSET_SEC = 3   -- test position: 3 s after the timeline start

local resolve = Resolve()
local pm      = resolve:GetProjectManager()
local project = pm:GetCurrentProject()
local tl      = project and project:GetCurrentTimeline()
if not tl then print("No timeline open.") return end
local mp      = project:GetMediaPool()
local fps     = tonumber(tl:GetSetting("timelineFrameRate")) or 25

local function items(i) return tl:GetItemListInTrack("subtitle", i) or {} end
local function snapshot()
  local s, n = {}, tl:GetTrackCount("subtitle")
  for i = 1, n do s[i] = #items(i) end
  return s, n
end
local function show(label, s, n)
  local parts = {}
  for i = 1, n do
    parts[#parts + 1] = "ST" .. i .. "=" .. s[i] .. (tl:GetIsTrackEnabled("subtitle", i) and " active" or "")
  end
  print(label .. " subtitle tracks: " .. n .. "  (" .. table.concat(parts, ", ") .. ")")
end

print("-------- Resolve " .. resolve:GetVersionString() .. "  |  variant " .. VARIANT)
print("Timeline '" .. tl:GetName() .. "'  start " .. tl:GetStartTimecode()
      .. " (frame " .. tl:GetStartFrame() .. ")  fps " .. fps)

-- Test SRT: first subtitle at 1 s
local srt = (os.getenv("TEMP") or ".") .. "\\wx_probe.srt"
local f = io.open(srt, "w")
f:write("1\n00:00:01,000 --> 00:00:03,000\nProbe V" .. VARIANT .. " one\n\n"
     .. "2\n00:00:04,000 --> 00:00:06,000\nProbe V" .. VARIANT .. " two\n\n")
f:close()

-- Target track: first empty subtitle track, otherwise a new one
local before, nBefore = snapshot()
show("Before:", before, nBefore)
local target
if VARIANT == 6 then
  target = 1
  if nBefore == 0 then
    tl:AddTrack("subtitle")
    before = { 0 }
    print("No subtitle track -> ST1 created")
  elseif before[1] > 0 then
    local ok = tl:AddTrack("subtitle", { index = 1 })
    print("ST1 in use -> new track inserted at position 1: " .. tostring(ok))
    local shifted = { 0 }
    for i = 1, nBefore do shifted[i + 1] = before[i] end
    before = shifted
    local s, n = snapshot()
    show("After inserting:", s, n)
  else
    print("Target track: ST1 (empty)")
  end
end
for i = 1, nBefore do if target then break end; if before[i] == 0 then target = i end end
if not target then
  tl:AddTrack("subtitle")
  target = tl:GetTrackCount("subtitle")
  before[target] = 0
  print("No empty track -> new track ST" .. target .. " created")
else
  print("Target track: ST" .. target .. " (empty)")
end

pm:SaveProject()
print("Project saved.")

local imported = mp:ImportMedia({ srt })
local item = imported and imported[1]
if not item then print("ImportMedia of the SRT failed.") return end

-- Variant 4/5: lock or disable the other tracks, remember their state
local restore = {}
local n = tl:GetTrackCount("subtitle")
if VARIANT == 4 or VARIANT == 5 then
  for i = 1, n do
    if i ~= target then
      if VARIANT == 4 then
        restore[i] = tl:GetIsTrackLocked("subtitle", i)
        tl:SetTrackLock("subtitle", i, true)
      else
        restore[i] = tl:GetIsTrackEnabled("subtitle", i)
        tl:SetTrackEnable("subtitle", i, false)
      end
    end
  end
  print((VARIANT == 4 and "Locked" or "Disabled") .. ": all subtitle tracks except ST" .. target)
end

if VARIANT == 7 then
  for i = 1, n do
    if i ~= target then tl:SetTrackEnable("subtitle", i, false) end
  end
  tl:SetTrackEnable("subtitle", target, true)
  local s, m = snapshot()
  show("Activated:", s, m)
end

local rec = tl:GetStartFrame() + math.floor(REC_OFFSET_SEC * fps + 0.5)
local res
if VARIANT == 1 or VARIANT == 6 or VARIANT == 7 then
  res = mp:AppendToTimeline({ item })
elseif VARIANT == 3 then
  res = mp:AppendToTimeline({ { mediaPoolItem = item, recordFrame = rec, trackIndex = target } })
else
  res = mp:AppendToTimeline({ { mediaPoolItem = item, recordFrame = rec } })
end

for i, v in pairs(restore) do
  if VARIANT == 4 then tl:SetTrackLock("subtitle", i, v) else tl:SetTrackEnable("subtitle", i, v) end
end

print("AppendToTimeline returned: " .. (res and (#res .. " items") or "nil"))
if VARIANT >= 2 and VARIANT <= 5 then
  print("Expected: first subtitle at frame " .. (rec + math.floor(fps + 0.5)) .. "  (recordFrame " .. rec .. " + 1 s)")
end

local after, nAfter = snapshot()
show("After:", after, nAfter)
local any = false
for i = 1, nAfter do
  local d = after[i] - (before[i] or 0)
  if d > 0 then
    any = true
    local first = items(i)[(before[i] or 0) + 1]
    print("-> " .. d .. " new subtitles on ST" .. i
          .. (i == target and "  (= target track)" or "  (NOT the target track)")
          .. (first and ("  first at frame " .. first:GetStart()) or ""))
  end
end
if not any then print("-> nothing placed") end

if VARIANT == 7 and any then
  local nr = 1
  for i = 1, nAfter do
    if i ~= target and (tl:GetTrackName("subtitle", i) or ""):find("^WhisperX") then nr = nr + 1 end
  end
  local name = "WhisperX " .. nr
  local ok = tl:SetTrackName("subtitle", target, name)
  print("Track name ST" .. target .. " -> '" .. tostring(tl:GetTrackName("subtitle", target))
        .. "'  (SetTrackName: " .. tostring(ok) .. ")")
end
