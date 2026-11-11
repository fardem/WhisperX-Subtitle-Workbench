-- probe_render.lua - which render settings Resolve takes for the range In to Out.
-- Renders nothing: each render job is deleted right away; Deliver settings, In/Out and page are restored.
-- Place: %APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Edit\
-- Start: Workspace -> Scripts -> probe_render. Output: Workspace -> Console (Lua) and %TEMP%\wx_probe_render.txt
--
-- Results with Resolve Studio 21.2.0.6 on Windows, timeline frames 1080000-1081846, In/Out 890-1502:
--   variant  calls return          range of the render job
--    1       -                     1080890-1081502
--    2       false                 1080890-1081502
--    3       true                  1080000-1081845, In/Out of the timeline cleared
--    4       true, false           1080000-1081845
--    5       true                  1080890-1081502
--    6       true, true, false     1080890-1081502
--    7       true                  1080890-1081502
--    8       false                 1080890-1081502
--    9       true, true            1080890-1081502
--   10       false                 1080890-1081502
--   11       true, true, false     1080890-1081502, .wav
--   12       true, true, true      1080890-1081502, .wav
--   13       true, false, false    1080890-1081502, .mov, GetRenderCodecs("wav") = {}
--   14       true, true            1080890-1081502, .wav
--   15       true, true            1080000-1081845, .wav, In/Out of the timeline cleared
--   GetRenderJobList has MarkIn and MarkOut; GetRenderSettings does not exist.

local resolve = Resolve()
local pm = resolve:GetProjectManager()
local project = pm and pm:GetCurrentProject()
local tl = project and project:GetCurrentTimeline()
if not tl then
  print("No timeline open.")
  return
end

local lines = {}
local function out(...)
  local parts = {}
  for i = 1, select("#", ...) do
    parts[#parts + 1] = tostring((select(i, ...)))
  end
  local s = table.concat(parts, " ")
  print(s)
  lines[#lines + 1] = s
end

local function show(v, depth)
  depth = depth or 0
  if type(v) ~= "table" then
    return tostring(v)
  end
  if depth > 2 then
    return "{...}"
  end
  local keys = {}
  for k in pairs(v) do
    keys[#keys + 1] = k
  end
  table.sort(keys, function(x, y) return tostring(x) < tostring(y) end)
  local parts = {}
  for _, k in ipairs(keys) do
    parts[#parts + 1] = tostring(k) .. "=" .. show(v[k], depth + 1)
  end
  return "{" .. table.concat(parts, ", ") .. "}"
end

-- "n/a" = method missing in this Resolve version
local function call(obj, method, ...)
  local okf, fn = pcall(function() return obj[method] end)
  if not okf or type(fn) ~= "function" then
    return "n/a"
  end
  local ok, a = pcall(fn, obj, ...)
  if not ok then
    return "error: " .. tostring(a)
  end
  return a
end

local function copy_marks(m)
  local c = {}
  for _, kind in ipairs({ "video", "audio" }) do
    if type(m) == "table" and type(m[kind]) == "table" then
      c[kind] = { ["in"] = m[kind]["in"], ["out"] = m[kind]["out"] }
    end
  end
  return c
end

local start, last = tl:GetStartFrame(), tl:GetEndFrame()
local fps = tonumber(tl:GetSetting("timelineFrameRate")) or 25
local saved = copy_marks(tl:GetMarkInOut())
local m = saved.audio or saved.video
if m and (m["in"] == nil) ~= (m["out"] == nil) then
  print("Set In and Out, or neither: SetMarkInOut cannot restore a single mark.")
  return
end
local a, b
if m and m["in"] then
  a, b = start + m["in"], start + m["out"]
else
  a, b = start + math.floor(fps + 0.5), math.min(start + math.floor(3 * fps + 0.5), last - 1)
end

local function restore_marks()
  for _, kind in ipairs({ "video", "audio" }) do
    local k = saved[kind]
    if k and k["in"] and k["out"] then
      tl:SetMarkInOut(k["in"], k["out"], kind)
    else
      tl:ClearMarkInOut(kind)
    end
  end
end

local function set(s)
  return function()
    out("  SetRenderSettings", show(s), "->", show(call(project, "SetRenderSettings", s)))
  end
end
local function preset(name)
  return function()
    out("  LoadRenderPreset", name, "->", show(call(project, "LoadRenderPreset", name)))
  end
end
local function mode(n)
  return function()
    out("  SetCurrentRenderMode", n, "->", show(call(project, "SetCurrentRenderMode", n)))
  end
end
local function codec(f, c)
  return function()
    out("  SetCurrentRenderFormatAndCodec", f, c, "->", show(call(project, "SetCurrentRenderFormatAndCodec", f, c)))
    out("  GetCurrentRenderFormatAndCodec", show(call(project, "GetCurrentRenderFormatAndCodec")))
  end
end

local VARIANTS = {
  { "no change (range of the Deliver page)" },
  { "SelectAllFrames=false alone", set({ SelectAllFrames = false }) },
  { "SelectAllFrames=true alone", set({ SelectAllFrames = true }) },
  { "true, then false", set({ SelectAllFrames = true }), set({ SelectAllFrames = false }) },
  { "false, MarkIn, MarkOut in one call", set({ SelectAllFrames = false, MarkIn = a, MarkOut = b }) },
  { "MarkIn, MarkOut, then false", set({ MarkIn = a }), set({ MarkOut = b }), set({ SelectAllFrames = false }) },
  { "MarkIn and MarkOut only", set({ MarkIn = a, MarkOut = b }) },
  { "0 instead of false", set({ SelectAllFrames = 0 }) },
  { "other key with false and true", set({ ExportVideo = false }), set({ ExportVideo = true }) },
  { "empty table", set({}) },
  { "plugin now: Audio Only, single clip, false alone",
    preset("Audio Only"), mode(1), set({ SelectAllFrames = false }) },
  { "Audio Only, single clip, one call",
    preset("Audio Only"), mode(1), set({ SelectAllFrames = false, MarkIn = a, MarkOut = b }) },
  { "wav after ExportVideo=false", set({ ExportVideo = false, ExportAudio = true }), codec("wav", "LinearPCM"),
    codec("wav", "Linear PCM") },
  { "AutoSubs: Audio Only, one call without SelectAllFrames", preset("Audio Only"),
    set({ RenderMode = "Single clip", ExportVideo = false, ExportAudio = true, IsExportVideo = false,
          IsExportAudio = true, AudioBitDepth = 16, AudioSampleRate = 48000, MarkIn = a, MarkOut = b }) },
  { "whole timeline via MarkIn/MarkOut", preset("Audio Only"), set({ MarkIn = start, MarkOut = last - 1 }) },
}

local page = resolve:GetCurrentPage()
local dir = os.getenv("TEMP") or "C:\\Temp"
out("-------- probe_render | Resolve", resolve:GetVersionString(), resolve:GetProductName(), "| page", page)
out("Timeline", tl:GetName(), "| start", start, tl:GetStartTimecode(), "| end", last, "| fps", fps)
out("GetMarkInOut", show(saved))
out("Range for the test: MarkIn", a, "MarkOut", b, m and m["in"] and "(In/Out of the timeline)" or "(no In/Out: 1 s to 3 s)")
out("OpenPage deliver ->", show(resolve:OpenPage("deliver")))
local backup = "WX-Probe-" .. os.date("%H%M%S")
out("SaveAsNewRenderPreset", backup, "->", show(call(project, "SaveAsNewRenderPreset", backup)))
out("GetCurrentRenderFormatAndCodec", show(call(project, "GetCurrentRenderFormatAndCodec")))
out("GetCurrentRenderMode", show(call(project, "GetCurrentRenderMode")))
local formats = call(project, "GetRenderFormats")
out("GetRenderFormats", show(formats))
if type(formats) == "table" then
  for name, ext in pairs(formats) do
    local n, e = tostring(name):lower(), tostring(ext):lower()
    if n:find("wav") or e:find("wav") then
      out("GetRenderCodecs", name, show(call(project, "GetRenderCodecs", name)))
      out("GetRenderCodecs", ext, show(call(project, "GetRenderCodecs", ext)))
    end
  end
end
local has_get = call(project, "GetRenderSettings") ~= "n/a"
out("GetRenderSettings", has_get and show(call(project, "GetRenderSettings")) or "n/a")

local ok, err = pcall(function()
  for n, v in ipairs(VARIANTS) do
    out(string.format("--- %d: %s", n, v[1]))
    call(project, "LoadRenderPreset", backup)
    restore_marks()
    for i = 2, #v do
      v[i]()
    end
    if has_get then
      out("  GetRenderSettings", show(call(project, "GetRenderSettings")))
    end
    out("  SetRenderSettings TargetDir, CustomName ->",
        show(call(project, "SetRenderSettings", { TargetDir = dir, CustomName = "wx_probe" })))
    local id = call(project, "AddRenderJob")
    out("  AddRenderJob ->", show(id))
    if type(id) == "string" and id ~= "" then
      local jobs = call(project, "GetRenderJobList")
      for _, j in ipairs(type(jobs) == "table" and jobs or {}) do
        if type(j) == "table" and (j["JobId"] == id or j["Id"] == id) then
          out("  Job: MarkIn", j["MarkIn"], "MarkOut", j["MarkOut"], "| RenderMode", j["RenderMode"],
              "| OutputFilename", j["OutputFilename"])
          if n == 1 then
            out("  Job (all keys):", show(j))
          end
        end
      end
      out("  DeleteRenderJob ->", show(call(project, "DeleteRenderJob", id)))
    end
    out("  GetMarkInOut", show(copy_marks(tl:GetMarkInOut())))
  end
end)
if not ok then
  out("Stopped with an error:", err)
end

out("--- Restore")
out("LoadRenderPreset", backup, "->", show(call(project, "LoadRenderPreset", backup)))
out("DeleteRenderPreset", backup, "->", show(call(project, "DeleteRenderPreset", backup)))
restore_marks()
out("GetMarkInOut", show(copy_marks(tl:GetMarkInOut())))
out("OpenPage", page, "->", show(resolve:OpenPage(page)))

-- io.open in Resolve's Lua takes ASCII paths only
local path = dir .. "\\wx_probe_render.txt"
local f = io.open(path, "w")
if f then
  f:write(table.concat(lines, "\n"), "\n")
  f:close()
  print("Saved: " .. path)
else
  print("Not saved (path with non-ASCII characters?): " .. path)
end
