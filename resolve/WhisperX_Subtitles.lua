--[[
WhisperX_Subtitles.lua - DaVinci Resolve plugin of the WhisperX Subtitle Workbench.
The Workbench installs it and writes its folder, language and texts into the WX-CONFIG block.

Resolve Studio 21.1.2 (resolve/tools/probe_subtitles.lua checks it): MediaPool:AppendToTimeline puts an SRT
only on the active subtitle track, after its last subtitle; on an empty track the SRT times stay relative to
the timeline start. trackIndex/recordFrame are ignored. Only one subtitle track is active, new ones are not.

io.open in Resolve's Lua cannot open non-ASCII paths on Windows, so every path here is ASCII.
Every step is logged to Workspace -> Console (lines starting with "[WhisperX]").

The render part follows AutoSubs (github.com/tmoroney/auto-subs, MIT License, Copyright (c) 2023 Tom Moroney):
Deliver settings saved as a temporary preset, render, restore, job status by Error/CompletionPercentage,
left-over own jobs removed. Own code.
]]

-- >>> WX-CONFIG (template - the Workbench writes the real values here when it installs the plugin) >>>
local WX = {
  version   = "0.7.1",
  workbench = nil,
  python    = nil,
  lang      = "en",
  texts     = {},
}
-- <<< WX-CONFIG <<<

local JOB_FORMAT    = "whisperx-resolve-job/1"
local PRESET_PREFIX = "WhisperX-Backup-"      -- temporary render preset with the user's Deliver settings
local BIN_NAME      = "WhisperX"              -- media pool bin for the imported SRTs
local AUDIO_NAME    = "audio"                 -- CustomName of the render -> audio.wav

local M = {}                                  -- functions (returned instead of running when WX_TEST is set)
local S = { state = "idle" }                  -- idle | busy | rendering | waiting | asking
M.S = S

-- ─── Basics ──────────────────────────────────────────────────────────────────

local function log(...)
  local parts = {}
  for i = 1, select("#", ...) do
    parts[#parts + 1] = tostring((select(i, ...)))
  end
  print("[WhisperX] " .. table.concat(parts, " "))
end
M.log = log

local function T(key, vars)
  local s = (WX.texts[WX.lang] or {})[key] or (WX.texts.en or {})[key] or key
  if vars then
    s = (s:gsub("{(%w+)}", function(k)
      local v = vars[k]
      if v == nil then
        return "{" .. k .. "}"
      end
      return tostring(v)
    end))
  end
  return s
end
M.T = T

local G_os      = rawget(_G, "os") or {}
local G_io      = rawget(_G, "io")
local G_jit     = rawget(_G, "jit")
local G_package = rawget(_G, "package")
local bmd       = rawget(_G, "bmd")

local IS_WIN = (G_jit ~= nil and G_jit.os == "Windows")
  or (G_package ~= nil and type(G_package.config) == "string" and G_package.config:sub(1, 1) == "\\")
  or (G_jit == nil and type(G_os.getenv) == "function" and G_os.getenv("WINDIR") ~= nil)
local SEP = IS_WIN and "\\" or "/"
local HAS_IO = type(G_io) == "table" and type(G_io.open) == "function"
local HAS_REMOVE = type(G_os.remove) == "function"
local HAS_EXECUTE = type(G_os.execute) == "function"
M.IS_WIN, M.SEP = IS_WIN, SEP

local function join(...)
  return table.concat({ ... }, SEP)
end
M.join = join

-- <Workbench>\resolve_temp\ holds the job folders and the SRTs of the last hand-overs
local TEMP = WX.workbench and join(WX.workbench, "resolve_temp") or nil
local LAST = TEMP
M.TEMP, M.LAST = TEMP, LAST

local function round(x)
  return math.floor(x + 0.5)
end

local function clean_error(err)
  -- "file.lua:123: message" -> "message"
  return (tostring(err):gsub("^[^\n]-:%d+: ", ""))
end

local function wait(seconds)
  if bmd and type(bmd.wait) == "function" then
    pcall(bmd.wait, seconds)
  end
end

local function fmt_dur(sec)
  sec = math.max(0, round(tonumber(sec) or 0))
  local h, m, s = math.floor(sec / 3600), math.floor(sec % 3600 / 60), sec % 60
  if h > 0 then
    return string.format("%d:%02d:%02d", h, m, s)
  end
  return string.format("%d:%02d", m, s)
end
M.fmt_dur = fmt_dur

-- ─── Resolve objects ─────────────────────────────────────────────────────────

local resolve = rawget(_G, "resolve")
if resolve == nil and type(rawget(_G, "Resolve")) == "function" then
  resolve = Resolve()
end
local fu = rawget(_G, "fu") or rawget(_G, "fusion")
if fu == nil and resolve ~= nil then
  local ok, f = pcall(function() return resolve:Fusion() end)
  fu = ok and f or nil
end

-- Call a method of a Resolve object; errors are logged (the same one only a few times), never thrown.
local api_problems = {}
local function api(obj, method, ...)
  if obj == nil then
    return nil
  end
  local okf, fn = pcall(function() return obj[method] end)
  if not okf or type(fn) ~= "function" then
    if not api_problems[method] then
      api_problems[method] = 1
      log("API not available:", method)
    end
    return nil
  end
  local ok, a, b = pcall(fn, obj, ...)
  if not ok then
    api_problems[method] = (api_problems[method] or 0) + 1
    if api_problems[method] <= 3 then
      log("API error in " .. method .. ":", a)
    end
    return nil
  end
  return a, b
end
M.api = api

local function current()
  local pm = api(resolve, "GetProjectManager")
  local project = pm and api(pm, "GetCurrentProject") or nil
  local tl = project and api(project, "GetCurrentTimeline") or nil
  return pm, project, tl
end

-- ─── Files ───────────────────────────────────────────────────────────────────

local function file_exists(path)
  if bmd and type(bmd.fileexists) == "function" then
    local ok, r = pcall(bmd.fileexists, path)
    if ok and r then
      return true
    end
  end
  if HAS_IO then
    local f = G_io.open(path, "rb")
    if f then
      f:close()
      return true
    end
  end
  return false
end
M.file_exists = file_exists

local function dir_exists(path)
  if bmd and type(bmd.direxists) == "function" then
    local ok, r = pcall(bmd.direxists, path)
    if ok then
      return r and true or false
    end
  end
  if HAS_IO then                                      -- no bmd: try to create a file in it
    local probe = path .. SEP .. ".wx_probe"
    local f = G_io.open(probe, "wb")
    if f then
      f:close()
      if HAS_REMOVE then pcall(G_os.remove, probe) end
      return true
    end
  end
  return false
end

local function size_of(path)
  if not HAS_IO then
    return nil
  end
  local f = G_io.open(path, "rb")
  if not f then
    return nil
  end
  local n = f:seek("end")
  f:close()
  return n
end

local function make_dir(path)
  if dir_exists(path) then
    return true
  end
  if bmd and type(bmd.createdir) == "function" then
    pcall(bmd.createdir, path)
    if dir_exists(path) then
      return true
    end
  end
  if HAS_EXECUTE then                                 -- last resort: shows a console window for a moment
    pcall(G_os.execute, (IS_WIN and 'mkdir "' or 'mkdir -p "') .. path .. '"')
  end
  return dir_exists(path)
end

local function remove_file(path)
  if not file_exists(path) then
    return true
  end
  if HAS_REMOVE then
    pcall(G_os.remove, path)
  end
  return not file_exists(path)
end

local function remove_dir(path)
  if bmd and type(bmd.removedir) == "function" then
    pcall(bmd.removedir, path)
  end
  return not dir_exists(path)
end

-- key=value files: one value per line, # starts a comment line.
local function parse_kv(text)
  if text:sub(1, 3) == "\239\187\191" then
    text = text:sub(4)                                -- UTF-8 BOM
  end
  local out = {}
  for line in (text .. "\n"):gmatch("(.-)\r?\n") do
    local s = line:match("^%s*(.-)%s*$")
    if s ~= "" and not s:match("^[#;]") then
      local k, v = s:match("^([^=]-)%s*=%s*(.*)$")
      if k and k ~= "" then
        out[k:lower()] = v
      end
    end
  end
  return out
end
M.parse_kv = parse_kv

local function read_text(path)
  if not HAS_IO then
    return nil
  end
  local f = G_io.open(path, "rb")
  if not f then
    return nil
  end
  local text = f:read("*a")
  f:close()
  return text
end

local function read_kv(path)
  local text = read_text(path)
  return text and parse_kv(text) or nil
end

local function write_kv(path, header, rows)
  if not HAS_IO then
    return false, "no io"
  end
  local f, err = G_io.open(path, "wb")
  if not f then
    return false, err
  end
  f:write("# ", header, "\n")
  for _, kv in ipairs(rows) do
    local v = kv[2]
    if v == nil then
      v = ""
    end
    f:write(kv[1], "=", (tostring(v):gsub("[\r\n]+", " ")), "\n")
  end
  f:close()
  return true
end
M.write_kv = write_kv

-- ─── Frames ──────────────────────────────────────────────────────────────────

local function parse_fps(value)
  local s = tostring(value or ""):match("%d+[%.,]?%d*")
  local n = s and tonumber((s:gsub(",", "."))) or nil
  if n == nil or n <= 0 then
    return nil
  end
  return n
end
M.parse_fps = parse_fps

-- 29.97 -> 30000/1001 (likewise 23.976, 47.952, 59.94, 119.88)
local function exact_fps(fps)
  for _, base in ipairs({ 24, 30, 48, 60, 120 }) do
    local ntsc = base * 1000 / 1001
    if math.abs(fps - ntsc) < 0.005 then
      return ntsc
    end
  end
  return fps
end
M.exact_fps = exact_fps

local function timeline_info(tl)
  local info = {}
  info.name = tostring(api(tl, "GetName") or "?")
  info.id = tostring(api(tl, "GetUniqueId") or "")
  local fps = api(tl, "GetSetting", "timelineFrameRate")
  info.fps = parse_fps(fps) or 25
  info.fps_text = (fps ~= nil and tostring(fps) ~= "") and tostring(fps) or tostring(info.fps)
  info.drop = tostring(api(tl, "GetSetting", "timelineDropFrameTimecode") or "0")
  info.start = tonumber(api(tl, "GetStartFrame")) or 0
  info.start_tc = tostring(api(tl, "GetStartTimecode") or "")
  info.last = tonumber(api(tl, "GetEndFrame")) or info.start
  return info
end
M.timeline_info = timeline_info

local function copy_marks(marks)
  local out = {}
  if type(marks) == "table" then
    for _, kind in ipairs({ "video", "audio" }) do
      local m = marks[kind]
      if type(m) == "table" then
        out[kind] = { ["in"] = tonumber(m["in"]), ["out"] = tonumber(m["out"]) }
      end
    end
  end
  return out
end

-- Range to render: In/Out if set (values of GetMarkInOut are relative to the timeline start),
-- otherwise the whole timeline. -> first frame, last frame (absolute), whole?, marks
local function render_range(tl, info)
  local marks = copy_marks(api(tl, "GetMarkInOut"))
  local m = marks.audio or marks.video
  if m and (m["in"] ~= nil or m["out"] ~= nil) then
    local a = info.start + (m["in"] or 0)
    local b = m["out"] ~= nil and (info.start + m["out"]) or (info.last - 1)
    if b >= a then
      return a, b, false, marks
    end
  end
  return info.start, info.last - 1, true, marks
end
M.render_range = render_range

local function is_playing(tl)
  if not (bmd and type(bmd.wait) == "function") then
    return false
  end
  local a = api(tl, "GetCurrentTimecode")
  wait(0.3)
  local b = api(tl, "GetCurrentTimecode")
  return a ~= nil and b ~= nil and a ~= "" and a ~= b
end

-- ─── Render ──────────────────────────────────────────────────────────────────

local function norm_dir(p)
  return (tostring(p or ""):gsub("\\", "/"):gsub("/+$", ""):lower())
end

local function is_our_dir(dir)
  local a, b = norm_dir(dir), norm_dir(TEMP)
  return a ~= "" and b ~= "" and a:sub(1, #b) == b
end
M.is_our_dir = is_our_dir

local function job_progress(project, id)
  local st = api(project, "GetRenderJobStatus", id)
  return type(st) == "table" and tonumber(st["CompletionPercentage"]) or nil
end

-- Our render jobs write into <Workbench>\resolve_temp\ - that path marks them as ours.
-- A left-over job of ours can keep IsRenderingInProgress() true forever. Jobs of others are never touched.
local function remove_stale_jobs(project, only_stalled)
  local jobs = api(project, "GetRenderJobList")
  if type(jobs) ~= "table" then
    return
  end
  for _, j in ipairs(jobs) do
    if type(j) == "table" then
      local id = j["JobId"] or j["Id"]
      if id ~= nil and id ~= S.render_id and is_our_dir(j["TargetDir"]) then
        local removable = true
        if only_stalled then
          local p1 = job_progress(project, id)
          wait(0.5)
          removable = p1 ~= nil and p1 == job_progress(project, id)
        end
        if removable then
          log("Removing a left-over render job of the plugin:", id, tostring(j["TargetDir"]))
          api(project, "DeleteRenderJob", id)
        end
      end
    end
  end
end

local function remove_stale_presets(project)
  local list = api(project, "GetRenderPresetList")
  if type(list) ~= "table" then
    return
  end
  for _, p in ipairs(list) do
    local name = p
    if type(p) == "table" then
      name = p["PresetName"] or p["Name"] or p["name"]
    end
    name = tostring(name or "")
    if name:sub(1, #PRESET_PREFIX) == PRESET_PREFIX and name ~= S.preset then
      log("Removing a left-over backup preset of the plugin:", name)
      api(project, "DeleteRenderPreset", name)
    end
  end
end

-- SetRenderSettings only answers for the whole table and ignores unknown keys:
-- set the keys one by one and check each.
local function setting(project, key, value, critical)
  local ok = api(project, "SetRenderSettings", { [key] = value })
  log("SetRenderSettings", key, "=", tostring(value), "->", tostring(ok))
  if critical and ok ~= true then
    error(T("err_setting", { key = key }), 0)
  end
  return ok
end

local function add_unique(list, value)
  value = tostring(value or "")
  if value == "" then
    return
  end
  for _, v in ipairs(list) do
    if v == value then
      return
    end
  end
  list[#list + 1] = value
end

-- WAV via format/codec - the stock preset "Audio Only" has a localized name.
local function set_wav(project)
  local formats = { "wav" }
  local known = api(project, "GetRenderFormats")
  if type(known) == "table" then
    for name, ext in pairs(known) do
      local n, e = tostring(name):lower(), tostring(ext):lower()
      if n == "wav" or n == "wave" or e == "wav" then
        add_unique(formats, name)
        add_unique(formats, ext)
      end
    end
  end
  for _, fmt in ipairs(formats) do
    local codecs = { "LinearPCM" }
    local list = api(project, "GetRenderCodecs", fmt)
    if type(list) == "table" then
      for _, id in pairs(list) do
        add_unique(codecs, id)
      end
    end
    for _, codec in ipairs(codecs) do
      local ok = api(project, "SetCurrentRenderFormatAndCodec", fmt, codec)
      log("SetCurrentRenderFormatAndCodec", fmt, codec, "->", tostring(ok))
      if ok == true then
        local cur = api(project, "GetCurrentRenderFormatAndCodec")
        if type(cur) == "table" then
          log("Render format now:", tostring(cur["format"]), tostring(cur["codec"]))
        end
        return true
      end
    end
  end
  local ok = api(project, "LoadRenderPreset", "Audio Only")
  log("LoadRenderPreset 'Audio Only' ->", tostring(ok))
  return ok == true
end

local function same_marks(a, b)
  for _, kind in ipairs({ "video", "audio" }) do
    local x, y = a[kind] or {}, b[kind] or {}
    if x["in"] ~= y["in"] or x["out"] ~= y["out"] then
      return false
    end
  end
  return true
end

local function restore_marks(tl, saved)
  local now = copy_marks(api(tl, "GetMarkInOut"))
  if same_marks(now, saved) then
    log("In/Out marks unchanged")
    return
  end
  for _, kind in ipairs({ "video", "audio" }) do
    local m = saved[kind]
    if m and m["in"] ~= nil and m["out"] ~= nil then
      log("SetMarkInOut", kind, m["in"], m["out"], "->", tostring(api(tl, "SetMarkInOut", m["in"], m["out"], kind)))
    else
      log("ClearMarkInOut", kind, "->", tostring(api(tl, "ClearMarkInOut", kind)))
    end
  end
end

-- Puts everything back: render job, Deliver settings, In/Out marks, page. Safe to call twice.
local function restore_render()
  local job = S.job or {}
  local project, tl = job.project, job.tl
  if S.render_id and project then
    log("DeleteRenderJob", S.render_id, "->", tostring(api(project, "DeleteRenderJob", S.render_id)))
  end
  S.render_id = nil
  if S.preset and project then
    log("Restoring the Deliver settings: LoadRenderPreset ->", tostring(api(project, "LoadRenderPreset", S.preset)),
        "DeleteRenderPreset ->", tostring(api(project, "DeleteRenderPreset", S.preset)))
  elseif S.fallback and project then
    local fc, mode = S.fallback.fc, S.fallback.mode
    if type(fc) == "table" and fc["format"] and fc["codec"] then
      log("Restoring format/codec ->", tostring(api(project, "SetCurrentRenderFormatAndCodec", fc["format"], fc["codec"])))
    end
    if mode ~= nil then
      log("Restoring render mode ->", tostring(api(project, "SetCurrentRenderMode", mode)))
    end
    api(project, "SetRenderSettings", { ExportVideo = true, ExportAudio = true, IsExportVideo = true,
                                        IsExportAudio = true, SelectAllFrames = true })
  end
  S.preset, S.fallback = nil, nil
  if S.marks and tl then
    restore_marks(tl, S.marks)
  end
  S.marks = nil
  if S.page then
    if api(resolve, "GetCurrentPage") ~= S.page then
      log("OpenPage", S.page, "->", tostring(api(resolve, "OpenPage", S.page)))
    end
  end
  S.page = nil
end

local function make_job_id()
  local clock = type(G_os.clock) == "function" and G_os.clock() or 0
  math.randomseed(os.time() + round(clock * 1000))
  return os.date("%Y%m%d-%H%M%S") .. "-" .. string.format("%04d", math.random(0, 9999))
end

local function start_render(project, tl)
  -- the page first: several render calls switch to Deliver on their own
  S.page = api(resolve, "GetCurrentPage")
  log("Step 1 - page:", tostring(S.page), "| Resolve", tostring(api(resolve, "GetVersionString")),
      tostring(api(resolve, "GetProductName")))
  if not api(project, "IsRenderingInProgress") then
    remove_stale_jobs(project, false)
  else
    remove_stale_jobs(project, true)
    wait(0.3)
  end
  if api(project, "IsRenderingInProgress") then
    error(T("busy_render"), 0)
  end

  local info = timeline_info(tl)
  local a, b, whole, marks = render_range(tl, info)
  S.marks = marks
  log(string.format("Step 2 - timeline '%s' id %s | fps %s (%.6f) drop %s | start %d (%s) end %d | %s %d-%d",
      info.name, info.id, info.fps_text, exact_fps(info.fps), info.drop, info.start, info.start_tc, info.last,
      whole and "whole timeline" or "In/Out", a, b))

  local id = make_job_id()
  local dir = join(TEMP, id)
  if not make_dir(dir) then
    error(T("err_folder", { path = dir }), 0)
  end
  S.job = { id = id, dir = dir, info = info, a = a, b = b, whole = whole, tl = tl, project = project }
  log("Step 3 - job folder:", dir)

  log("Step 4 - OpenPage deliver ->", tostring(api(resolve, "OpenPage", "deliver")))
  remove_stale_presets(project)
  local preset = PRESET_PREFIX .. id
  if api(project, "SaveAsNewRenderPreset", preset) == true then
    S.preset = preset
    log("Deliver settings saved as preset", preset)
  else
    S.fallback = { fc = api(project, "GetCurrentRenderFormatAndCodec"), mode = api(project, "GetCurrentRenderMode") }
    log("SaveAsNewRenderPreset failed - remembering format/codec and render mode instead")
  end

  if not set_wav(project) then
    error(T("err_wav"), 0)
  end
  log("SetCurrentRenderMode single clip ->", tostring(api(project, "SetCurrentRenderMode", 1)))
  setting(project, "SelectAllFrames", whole, true)
  if not whole then
    setting(project, "MarkIn", a, true)
    setting(project, "MarkOut", b, true)
  end
  setting(project, "TargetDir", dir, true)
  setting(project, "CustomName", AUDIO_NAME, true)
  setting(project, "ExportVideo", false)
  setting(project, "IsExportVideo", false)
  setting(project, "ExportAudio", true)
  setting(project, "IsExportAudio", true)
  setting(project, "AudioSampleRate", 48000)
  setting(project, "AudioBitDepth", 16)

  local rid = api(project, "AddRenderJob")
  log("Step 5 - AddRenderJob ->", tostring(rid))
  if rid == nil or rid == "" then
    error(T("err_add_job"), 0)
  end
  S.render_id = rid
  local started = api(project, "StartRendering", rid)
  log("StartRendering ->", tostring(started))
  if not started then                                -- right after a preset change it sometimes says no
    wait(1.0)
    started = api(project, "StartRendering", rid)
    log("StartRendering (second try) ->", tostring(started))
  end
  if not started then
    error(T("err_start_render"), 0)
  end
  S.render_started = os.time()
  S.restarted = false
end

local function output_file(project)
  local jobs = api(project, "GetRenderJobList")
  if type(jobs) == "table" then
    for _, j in ipairs(jobs) do
      if type(j) == "table" and (j["JobId"] == S.render_id or j["Id"] == S.render_id) then
        local name = tostring(j["OutputFilename"] or "")
        if name ~= "" then
          return join(S.job.dir, name), name
        end
      end
    end
  end
  return join(S.job.dir, AUDIO_NAME .. ".wav"), AUDIO_NAME .. ".wav"
end

-- ─── Workbench ───────────────────────────────────────────────────────────────

local function get_ffi()
  local f = rawget(_G, "ffi")
  if type(f) == "table" then
    return f
  end
  local req = rawget(_G, "require")
  if type(req) == "function" then
    local ok, m = pcall(req, "ffi")
    if ok and type(m) == "table" then
      return m
    end
  end
  return nil
end

local function quote(s)
  return '"' .. s .. '"'
end

-- Start pythonw.exe whisper_tool.py <wav> without waiting and without a console window.
-- A Workbench that is already open takes the file over (and keeps its models loaded).
local function launch_workbench(wav)
  local params = quote(join(WX.workbench, "whisper_tool.py")) .. " " .. quote(wav)
  if IS_WIN then
    local ffi = get_ffi()
    if ffi then
      local ok, res = pcall(function()
        pcall(ffi.cdef, "void* ShellExecuteA(void* hwnd, const char* op, const char* file, "
                     .. "const char* params, const char* dir, int show);")
        local shell32 = ffi.load("shell32")
        local h = shell32.ShellExecuteA(nil, "open", WX.python, params, WX.workbench, 1)
        return tonumber(ffi.cast("intptr_t", h))
      end)
      log("ShellExecuteA ->", tostring(res))
      if ok and type(res) == "number" and res > 32 then
        return true
      end
    end
    if HAS_EXECUTE then                               -- shows a console window for a moment
      local cmd = 'start "" ' .. quote(WX.python) .. " " .. params
      log("os.execute:", cmd)
      local r = G_os.execute(cmd)
      return r == 0 or r == true
    end
  elseif HAS_EXECUTE then
    local cmd = quote(WX.python) .. " " .. params .. " >/dev/null 2>&1 &"
    log("os.execute:", cmd)
    local r = G_os.execute(cmd)
    return r == 0 or r == true
  end
  return false
end

local function write_job_file(project, wav_name)
  local job, info = S.job, S.job.info
  local rows = {
    { "format", JOB_FORMAT }, { "plugin", WX.version }, { "job", job.id }, { "audio", wav_name },
    { "timeline", info.name }, { "timeline_id", info.id }, { "project", api(project, "GetName") or "" },
    { "fps", info.fps_text }, { "drop_frame", info.drop }, { "start_frame", info.start },
    { "start_tc", info.start_tc }, { "range", job.whole and "all" or "inout" }, { "in_frame", job.a },
    { "out_frame", job.b }, { "end_frame", info.last }, { "resolve", api(resolve, "GetVersionString") or "" },
    { "language", S.choice_lang or "" }, { "mode", S.choice_mode or "" },
    { "filter", S.choice_filter and "1" or "0" },
    { "created", os.date("%Y-%m-%d %H:%M:%S") },
  }
  local ok, err = write_kv(join(job.dir, "job.txt"), "WhisperX Subtitle Workbench - DaVinci Resolve job", rows)
  if not ok then
    error(T("err_write", { path = join(job.dir, "job.txt"), why = tostring(err) }), 0)
  end
end

-- Remove the job folder. Files the Workbench created are listed in done.txt (files=...).
-- What cannot be deleted here is removed by the Workbench later (marker file).
local function delete_job_files(marker, extra)
  local job = S.job
  if not job or not job.dir then
    return
  end
  if marker then
    write_kv(join(job.dir, marker), "WhisperX", { { "job", job.id } })
  end
  local names = { job.wav_name or (AUDIO_NAME .. ".wav"), "job.txt", "done.txt", "done.txt.tmp" }
  for name in tostring(extra or ""):gmatch("[^;]+") do
    if not name:find("[/\\]") and name ~= ".." then
      names[#names + 1] = name
    end
  end
  local left = {}
  for _, name in ipairs(names) do
    if not remove_file(join(job.dir, name)) then
      left[#left + 1] = name
    end
  end
  if marker then
    remove_file(join(job.dir, marker))
  end
  remove_dir(job.dir)
  if dir_exists(job.dir) then
    -- something is left (still open, or unknown files): the marker tells the Workbench to remove it
    write_kv(join(job.dir, marker or "imported.txt"), "WhisperX", { { "job", job.id } })
    log("Job folder not removed completely (" .. table.concat(left, ", ") .. ") - the Workbench cleans it up later")
  else
    log("Job folder removed:", job.dir)
  end
end

-- ─── Subtitle tracks ─────────────────────────────────────────────────────────

-- Clips per subtitle track. Counted with #: pairs() reports 1 on an empty track.
local function track_counts(tl)
  local n = tonumber(api(tl, "GetTrackCount", "subtitle")) or 0
  local counts = {}
  for i = 1, n do
    local items = api(tl, "GetItemListInTrack", "subtitle", i)
    counts[i] = type(items) == "table" and #items or 0
  end
  return counts, n
end
M.track_counts = track_counts

local function counts_text(counts, n)
  local parts = {}
  for i = 1, n do
    parts[#parts + 1] = "ST" .. i .. "=" .. tostring(counts[i])
  end
  return table.concat(parts, " ")
end

-- Track rule: the first empty (and unlocked) subtitle track, otherwise a new one.
local function pick_track(tl)
  local counts, n = track_counts(tl)
  log("Subtitle tracks before:", n, counts_text(counts, n))
  for i = 1, n do
    if counts[i] == 0 then
      if api(tl, "GetIsTrackLocked", "subtitle", i) == true then
        log("ST" .. i .. " is empty but locked - skipped")
      else
        return i, false
      end
    end
  end
  local ok = api(tl, "AddTrack", "subtitle")
  local m = tonumber(api(tl, "GetTrackCount", "subtitle")) or 0
  log("AddTrack subtitle ->", tostring(ok), "| tracks now:", m)
  if m <= n then
    return nil
  end
  return m, true
end
M.pick_track = pick_track

-- Only one subtitle track can be active: deactivate all others, then activate the target.
local function activate_only(tl, target)
  local n = tonumber(api(tl, "GetTrackCount", "subtitle")) or 0
  for i = 1, n do
    if i ~= target then
      api(tl, "SetTrackEnable", "subtitle", i, false)
    end
  end
  local ok = api(tl, "SetTrackEnable", "subtitle", target, true)
  local on = api(tl, "GetIsTrackEnabled", "subtitle", target)
  log("Activate ST" .. target, "->", tostring(ok), "| active:", tostring(on))
  return on == true
end
M.activate_only = activate_only

local function track_name(tl, base, target)
  local used = {}
  local n = tonumber(api(tl, "GetTrackCount", "subtitle")) or 0
  for i = 1, n do
    if i ~= target then
      used[tostring(api(tl, "GetTrackName", "subtitle", i) or "")] = true
    end
  end
  if not used[base] then
    return base
  end
  local k = 2
  while used[base .. " " .. k] do
    k = k + 1
  end
  return base .. " " .. k
end
M.track_name = track_name

local function whisperx_bin(mp)
  local root = api(mp, "GetRootFolder")
  local subs = api(root, "GetSubFolderList")
  if type(subs) == "table" then
    for _, f in ipairs(subs) do
      if api(f, "GetName") == BIN_NAME then
        return f
      end
    end
  end
  local f = api(mp, "AddSubFolder", root, BIN_NAME)
  log("AddSubFolder", BIN_NAME, "->", tostring(f ~= nil))
  return f
end

local function first_cue_in_file(path)
  local text = read_text(path)
  if not text then
    return nil
  end
  local first
  for h, m, s, ms in text:gmatch("(%d+):(%d+):(%d+)[,.](%d+)%s*%-%->") do
    local t = (tonumber(h) * 60 + tonumber(m)) * 60 + tonumber(s) + tonumber((ms .. "00"):sub(1, 3)) / 1000
    if first == nil or t < first then
      first = t
    end
  end
  return first
end
M.first_cue_in_file = first_cue_in_file

local function count_cues(path)
  local text = read_text(path)
  if not text then
    return nil
  end
  local n = 0
  for _ in text:gmatch("%d+:%d+:%d+[,.]%d+%s*%-%->") do
    n = n + 1
  end
  return n
end
M.count_cues = count_cues

-- SRT -> media pool bin "WhisperX" -> track by the rule -> activate -> append -> check -> name.
-- expected = number of subtitles in the SRT (a different count on the track is reported)
-- -> ok, message, warning, track, name, placed
local function place_srt(project, tl, srt, base, first, expected)
  local mp = api(project, "GetMediaPool")
  local prev = api(mp, "GetCurrentFolder")
  local bin = whisperx_bin(mp)
  if bin then
    api(mp, "SetCurrentFolder", bin)
  end
  local imported = api(mp, "ImportMedia", { srt })
  if prev then
    api(mp, "SetCurrentFolder", prev)
  end
  local item = type(imported) == "table" and imported[1] or nil
  local props = item and api(item, "GetClipProperty")
  props = type(props) == "table" and props or {}
  log("ImportMedia", srt, "->", item and "ok" or "nothing", "| type:", tostring(props["Type"]),
      "| clip:", tostring(props["Clip Name"]), "| file:", tostring(props["File Path"]))
  if not item then
    return false, T("import_failed", { why = "ImportMedia" })
  end

  local target, created = pick_track(tl)
  if not target then
    return false, T("import_failed", { why = "AddTrack" })
  end
  log("Target track: ST" .. target, created and "(new)" or "(empty)")
  local before, n_before = track_counts(tl)
  if not activate_only(tl, target) then
    if created then
      api(tl, "DeleteTrack", "subtitle", target)
    end
    return false, T("not_active", { track = target })
  end

  local res = api(mp, "AppendToTimeline", { item })
  log("AppendToTimeline ->", type(res) == "table" and (#res .. " item(s)") or tostring(res))
  local after, n_after = track_counts(tl)
  log("Subtitle tracks after:", n_after, counts_text(after, n_after))
  for i = 1, n_after do
    local d = (after[i] or 0) - (before[i] or 0)
    if d ~= 0 then
      log("  ST" .. i .. ": " .. d .. " new" .. (i == target and " (= target)" or " (NOT the target)"))
    end
  end
  local added = (after[target] or 0) - (before[target] or 0)
  if added <= 0 then
    if created then
      api(tl, "DeleteTrack", "subtitle", target)
    end
    return false, T("not_placed", { track = target })
  end

  local warning
  expected = tonumber(expected)
  if expected and expected > 0 and added ~= expected then
    log("Placed", added, "subtitles, the SRT has", expected)
    warning = T("count_off", { placed = added, track = target, cues = expected })
  end
  local items = api(tl, "GetItemListInTrack", "subtitle", target)
  local actual
  if type(items) == "table" then
    for _, it in ipairs(items) do
      local s = tonumber(api(it, "GetStart"))
      if s and (actual == nil or s < actual) then
        actual = s
      end
    end
  end
  if first and actual then
    local start = tonumber(api(tl, "GetStartFrame")) or 0
    local fps = exact_fps(parse_fps(api(tl, "GetSetting", "timelineFrameRate")) or 25)
    local expected = start + round(first * fps)
    local diff = actual - expected
    log(string.format("First subtitle at frame %d, expected %d (start %d + %.3f s x %.6f fps) - difference %d",
        actual, expected, start, first, fps, diff))
    if math.abs(diff) > 1 then
      warning = (warning and (warning .. "\n") or "") .. T("position_off", { actual = actual, expected = expected, diff = diff,
                                    sec = string.format("%.3f", diff / fps) })
      if start ~= 0 and math.abs(math.abs(diff) - start) <= 1 then
        warning = warning .. "\n" .. T("position_start_tc")
        log("The difference equals the start frame of the timeline - the start timecode seems to count.")
      end
    end
  else
    log("Position not checked (first subtitle unknown)")
  end

  local name = track_name(tl, base, target)
  log("SetTrackName ST" .. target, name, "->", tostring(api(tl, "SetTrackName", "subtitle", target, name)),
      "| now:", tostring(api(tl, "GetTrackName", "subtitle", target)))
  return true, T("placed", { n = added, track = target, name = name }), warning, target, name, added
end
M.place_srt = place_srt

local function find_timeline(project, id)
  local count = tonumber(api(project, "GetTimelineCount")) or 0
  for i = 1, count do
    local t = api(project, "GetTimelineByIndex", i)
    if t and api(t, "GetUniqueId") == id then
      return t
    end
  end
  return nil
end

-- Date-time stamp the Workbench puts behind the language: _20261105-151530
local STAMP = "_%d%d%d%d%d%d%d%d%-%d%d%d%d%d%d"

local function lang_from_name(path)
  local name = tostring(path):match("[^/\\]+$") or tostring(path)
  name = name:gsub(STAMP .. "(%.[sS][rR][tT])$", "%1")
  local code = name:match("_(%a%a)%.[sS][rR][tT]$") or name:match("_(%u%u%u)%.[sS][rR][tT]$")
  return code and code:upper() or nil
end
M.lang_from_name = lang_from_name

local function clean_lang(text)
  local s = tostring(text or ""):gsub("[^%w%-]", ""):upper():sub(1, 8)
  if s == "" then
    return nil
  end
  return s
end
M.clean_lang = clean_lang

-- ─── Window ──────────────────────────────────────────────────────────────────

local ui, disp, win, itm

local function set_text(id, text)
  if itm and itm[id] then
    itm[id].Text = text
  end
end

-- Status line of the window; also logged, except the updates every second (quiet).
local function status(text, quiet)
  set_text("Status", text)
  if quiet then
    return
  end
  for line in (tostring(text) .. "\n"):gmatch("(.-)\n") do
    if line ~= "" then
      log(">> " .. line)
    end
  end
end
M.status = status

local function show(id, visible)
  if itm and itm[id] then
    itm[id].Hidden = not visible
  end
end

-- UIManager lays a window out for a different size than it draws, and widgets that were
-- hidden at the first layout pile up until the layout is calculated again: recalculate
-- now and once more on the next tick, after the event loop has drawn the change.
local function relayout()
  if win then
    pcall(function() win:RecalcLayout() end)
  end
  S.relayout = true
end

-- After an import (or a question about it) the window comes to the front and stays there
-- until OK / close. Resolve owns the window, so it stays above Resolve while you work.
local function to_front(done)
  if not win then
    return
  end
  pcall(function() win:Show() end)
  pcall(function() win:Raise() end)
  pcall(function() win:ActivateWindow() end)
  if done then
    set_text("Close", T("btn_ok"))
  end
  relayout()
  log("Window brought to the front")
end

-- Where the job stands in the Workbench (progress.txt in the job folder, written by the Workbench).
local function progress_text(p)
  if type(p) ~= "table" or not p.state then
    return T("waiting_do")
  end
  local pct = tonumber(p.pct) or 0
  if p.state == "countdown" then
    return T("wb_countdown", { n = p.n or "?" })
  elseif p.state == "running" then
    if p.stage == "asr" then
      return T("wb_asr", { pct = pct })
    elseif p.stage == "align" then
      return T("wb_align", { pct = pct })
    end
    return T("wb_loading")
  elseif p.state == "done" then
    return T("wb_done")
  elseif p.state == "error" then
    return T("wb_error")
  elseif p.state == "cancelled" then
    return T("wb_cancelled")
  end
  return T("wb_queued")
end
M.progress_text = progress_text

local function waiting_text(seconds, progress)
  return T("waiting", { time = fmt_dur(seconds) }) .. "\n" .. progress_text(progress)
      .. "\n\n" .. T("hint_changes")
end

local function buttons()
  if not itm then
    return
  end
  local idle = S.state == "idle"
  itm.Transcribe.Enabled = idle and S.can_transcribe == true
  itm.ImportSrt.Enabled = idle and S.can_import == true
  for _, id in ipairs({ "Lang", "Mode", "Filter" }) do
    if itm[id] then
      itm[id].Enabled = idle
    end
  end
  itm.Cancel.Enabled = S.state == "rendering" or S.state == "waiting" or S.state == "asking"
end

local function hide_question()
  for _, id in ipairs({ "Answer", "Yes", "No" }) do
    show(id, false)
  end
  relayout()
end

-- Question in the window (no blocking dialog): the status line asks, cb(yes, text) is
-- called on Yes/No.
local function ask(question, yes_text, no_text, cb, input)
  S.ask_cb, S.ask_from = cb, S.state
  S.state = "asking"
  status(question)
  set_text("Yes", yes_text)
  set_text("No", no_text)
  show("Yes", true)
  show("No", true)
  show("Answer", input ~= nil)
  if input ~= nil then
    set_text("Answer", input)
  end
  relayout()
  buttons()
end
M.ask = ask

local function answer(yes)
  local cb = S.ask_cb
  if not cb then
    return
  end
  local text = itm and itm.Answer and itm.Answer.Text or ""
  S.ask_cb = nil
  S.state = "busy"
  hide_question()
  log("Answer:", yes and "yes" or "no", text ~= "" and ("'" .. tostring(text) .. "'") or "")
  local ok, err = pcall(cb, yes, text)
  if not ok then
    log("Error:", err)
    status(T("error", { why = clean_error(err) }))
  end
  if S.state == "busy" then
    S.state = S.job and "waiting" or "idle"
  end
  buttons()
end
M.answer = answer

local function refresh_info()
  local _, project, tl = current()
  if not project then
    set_text("Info", T("no_project"))
    return
  end
  if not tl then
    set_text("Info", T("no_timeline"))
    return
  end
  local info = timeline_info(tl)
  local a, b, whole = render_range(tl, info)
  local dur = fmt_dur((b - a + 1) / exact_fps(info.fps))
  set_text("Info", T("timeline", { name = info.name }) .. "\n"
      .. T(whole and "range_all" or "range_inout", { dur = dur }))
end

-- ─── Remembered between runs (resolve_temp\plugin.txt) ───────────────────────

local SETTINGS = TEMP and join(TEMP, "plugin.txt") or nil

local function choices()
  return {
    lang = { { "", T("opt_workbench") }, { "auto", T("lang_auto") }, { "de", T("lang_de") }, { "en", T("lang_en") },
             { "tr", T("lang_tr") }, { "ar", T("lang_ar") } },
    mode = { { "", T("opt_workbench") }, { "sentence", T("mode_sentence") }, { "word", T("mode_word") },
             { "raw", T("mode_raw") } },
  }
end

local function index_of(list, code)
  for i, item in ipairs(list) do
    if item[1] == code then
      return i
    end
  end
  return 1
end

-- Window: start size, smallest size; LAYOUT changes when they do (an older saved size is dropped).
local WIN_SIZE, WIN_MIN, LAYOUT = { 600, 330 }, { 520, 290 }, "2"

-- Window position and size as saved; nothing unusable (off screen, tiny, older layout).
-- Resolve may report a size below MinimumSize (e.g. 505 wide) - that counts as usable.
local function saved_geometry(v)
  if tostring(v.layout or "") ~= LAYOUT then
    return nil
  end
  local x, y, w, h = tostring(v.geometry or ""):match("^(-?%d+),(-?%d+),(%d+),(%d+)$")
  x, y, w, h = tonumber(x), tonumber(y), tonumber(w), tonumber(h)
  if x and w >= 300 and h >= 200 and w <= 4000 and h <= 3000
      and x > -200 and y > -200 and x < 8000 and y < 5000 then
    return { x, y, w, h }
  end
  return nil
end
M.saved_geometry = saved_geometry

-- Current position and size of the window (several ways, the first one that answers counts).
local function window_geometry()
  local getters = {
    function() return itm.WhisperXWin.Geometry end,
    function() return win.Geometry end,
    function() return win:GetGeometry() end,
  }
  for _, get in ipairs(getters) do
    local ok, g = pcall(get)
    if ok and type(g) == "table" and tonumber(g[1]) and tonumber(g[2]) and tonumber(g[3]) and tonumber(g[4]) then
      return { math.floor(g[1]), math.floor(g[2]), math.floor(g[3]), math.floor(g[4]) }
    end
  end
  return nil
end

local function save_settings()
  if not SETTINGS or not HAS_IO then
    return
  end
  local rows = { { "lang", S.choice_lang or "" }, { "mode", S.choice_mode or "" },
                 { "filter", S.choice_filter and "1" or "0" }, { "layout", LAYOUT } }
  local g = window_geometry()
  if g then
    rows[#rows + 1] = { "geometry", string.format("%d,%d,%d,%d", g[1], g[2], g[3], g[4]) }
  end
  make_dir(TEMP)
  local saved = write_kv(SETTINGS, "WhisperX plugin window", rows)
  log("Window settings saved ->", tostring(saved), "| geometry:", g and table.concat(g, ",") or "not readable")
end

-- ─── Steps ───────────────────────────────────────────────────────────────────

local function end_job(marker, extra)
  delete_job_files(marker, extra)
  S.job, S.done = nil, nil
  S.state = "idle"
  buttons()
end

local function fail_render(why)
  log("Render failed:", why)
  restore_render()
  end_job("cancel.txt")
  status(T("render_failed", { why = why }))
end

local function after_render(project)
  local path, name = output_file(project)
  local size = size_of(path)
  log("Rendered file:", path, "| size:", tostring(size))
  if not size or size <= 0 then
    return fail_render(T("render_no_wav", { path = path }))
  end
  S.job.wav_name = name
  restore_render()
  write_job_file(project, name)
  log("Step 6 - job file written:", join(S.job.dir, "job.txt"))
  S.state = "waiting"
  S.wait_started = os.time()
  buttons()
  if launch_workbench(path) then
    status(waiting_text(0))
  else
    S.launch_failed = path
    status(T("launch_failed", { path = path }) .. "\n\n" .. T("hint_changes"))
  end
end

local function check_render()
  local project = S.job.project
  local busy = api(project, "IsRenderingInProgress")
  local st = api(project, "GetRenderJobStatus", S.render_id)
  st = type(st) == "table" and st or {}
  local pct = tonumber(st["CompletionPercentage"])
  if busy then
    status(T("rendering", { pct = pct and (pct .. " %") or "" }), true)
    return
  end
  log("Render status:", tostring(st["JobStatus"]), "| percent:", tostring(pct), "| error:", tostring(st["Error"]))
  local err = tostring(st["Error"] or "")
  if err ~= "" then
    return fail_render(err)
  end
  local path = output_file(project)
  local size = size_of(path)
  if pct == 100 or (pct == nil and size and size > 0) then
    return after_render(project)
  end
  if (pct == nil or pct == 0) and os.time() - (S.render_started or 0) < 5 then
    return                                           -- not started yet
  end
  if (pct == nil or pct == 0) and not S.restarted then
    S.restarted = true
    S.render_started = os.time()
    log("The render job has not started - starting it once more ->",
        tostring(api(project, "StartRendering", S.render_id)))
    return
  end
  return fail_render(T("render_incomplete", { pct = tostring(pct or 0) }))
end

local function import_now(project, tl)
  local done = S.done
  local pm = api(resolve, "GetProjectManager")
  local page = api(resolve, "GetCurrentPage")
  if page ~= "edit" and page ~= "cut" then
    log("OpenPage edit ->", tostring(api(resolve, "OpenPage", "edit")))
  end
  log("SaveProject before the import ->", tostring(api(pm, "SaveProject")))
  local ok, msg, warning, main = place_srt(project, tl, done.srt, "WhisperX " .. (done.lang or "XX"),
                                           tonumber(done.first), tonumber(done.cues))
  if ok then
    -- gaps, too fast subtitles and unsure words, each on its own track (to check, then delete the track)
    local names = { gaps = T("track_gaps"), fast = T("track_fast"), score = T("track_score") }
    for _, key in ipairs({ "gaps", "fast", "score" }) do
      if done[key] and done[key] ~= "" then
        local eok, emsg, ewarn, etrack, ename, en = place_srt(project, tl, done[key], names[key], nil,
                                                              tonumber(done[key .. "_n"]))
        log("Extra track", key, "->", tostring(eok), emsg)
        if eok then
          msg = msg .. "\n" .. T("placed_extra", { n = en, track = etrack, name = ename })
          if ewarn then
            warning = (warning and (warning .. "\n") or "") .. ewarn
          end
        end
      end
    end
    activate_only(tl, main)                           -- the transcript stays the visible track
  end
  end_job(nil, done.files)
  if ok then
    status(msg .. (warning and ("\n" .. warning) or ""))
  else
    status(msg .. "\n" .. T("srt_kept", { path = done.srt }))
  end
  to_front(true)
end

local function check_done()
  local job = S.job
  if not file_exists(join(job.dir, "job.txt")) then
    log("job.txt is gone:", job.dir)
    S.job = nil
    S.state = "idle"
    buttons()
    status(T("job_gone"))
    return
  end
  local done = read_kv(join(job.dir, "done.txt"))
  if not done then
    if not S.launch_failed then
      status(waiting_text(os.time() - (S.wait_started or os.time()), read_kv(join(job.dir, "progress.txt"))), true)
    end
    return
  end
  if not done.srt or done.srt == "" then
    log("done.txt without srt= - waiting for the next check")
    return
  end
  local _, project, cur = current()
  if cur and is_playing(cur) then                     -- switching pages during playback can hang Resolve
    status(T("playing"), true)
    return
  end
  S.done = done
  log("Step 7 - result:", done.srt, "| language:", tostring(done.lang), "| subtitles:", tostring(done.cues),
      "| first:", tostring(done.first), "| offset:", tostring(done.offset))
  S.state = "busy"
  buttons()

  local tl = project and find_timeline(project, job.info.id)
  if not tl then
    end_job(nil, done.files)
    status(T("timeline_gone", { name = job.info.name, path = done.srt }))
    to_front(true)
    return
  end
  if cur == nil or api(cur, "GetUniqueId") ~= job.info.id then
    log("SetCurrentTimeline", job.info.name, "->", tostring(api(project, "SetCurrentTimeline", tl)))
  end
  local last = tonumber(api(tl, "GetEndFrame"))
  if last ~= job.info.last then
    log("End frame changed:", job.info.last, "->", tostring(last))
    ask(T("end_changed", { old = job.info.last, new = tostring(last) }), T("btn_import_anyway"), T("btn_no"),
      function(yes)
        if yes then
          import_now(project, tl)
        else
          end_job(nil, done.files)
          status(T("import_skipped", { path = done.srt }))
          to_front(true)
        end
      end)
    to_front(false)
    return
  end
  import_now(project, tl)
end

local function tick()
  if S.ticking then
    return
  end
  S.ticking = true
  if S.relayout then                                  -- second layout pass, see relayout()
    S.relayout = false
    pcall(function() win:RecalcLayout() end)
  end
  local ok, err = pcall(function()
    if S.state == "idle" then
      refresh_info()
    elseif S.state == "rendering" then
      check_render()
    elseif S.state == "waiting" then
      check_done()
    end
  end)
  S.ticking = false
  if not ok then
    log("Error:", err)
    if S.state == "rendering" then
      fail_render(clean_error(err))
    else
      S.state = S.job and "waiting" or "idle"
      buttons()
      status(T("error", { why = clean_error(err) }))
    end
  end
end
M.tick = tick

function M.transcribe()
  if S.state ~= "idle" then
    return
  end
  local _, project, tl = current()
  if not project then
    return status(T("no_project"))
  end
  if not tl then
    return status(T("no_timeline"))
  end
  if is_playing(tl) then
    return status(T("playing"))
  end
  S.state = "busy"
  S.launch_failed = nil
  set_text("Close", T("btn_close"))
  buttons()
  status(T("starting"))
  local ok, err = pcall(start_render, project, tl)
  if ok then
    S.state = "rendering"
    buttons()
    status(T("rendering", { pct = "" }))
    return
  end
  log("Render not started:", err)
  if S.job == nil then
    S.job = { project = project, tl = tl }
  end
  fail_render(clean_error(err))
end

-- Resolve does not read a path again that is already in the media pool: it places what it read
-- the first time (e.g. the SRT before it was corrected). So a chosen file is imported as a copy
-- with a new name in resolve_temp\ - the next hand-over of the Workbench removes it.
local function fresh_copy(path)
  local text = read_text(path)
  if not (text and TEMP and make_dir(TEMP)) then
    return path
  end
  local name = (tostring(path):match("[^/\\]+$") or "import.srt"):gsub("%.[sS][rR][tT]$", "")
  name = name:gsub(STAMP .. "$", ""):gsub("[^%w%-_]", "_")
  local stamp = os.date("%Y%m%d-%H%M%S")
  local copy = join(TEMP, name .. "_" .. stamp .. ".srt")
  for i = 2, 99 do                                    -- twice in the same second
    if not file_exists(copy) then
      break
    end
    copy = join(TEMP, name .. "_" .. stamp .. "-" .. i .. ".srt")
  end
  local f = G_io.open(copy, "wb")
  if not f then
    return path
  end
  f:write(text)
  f:close()
  log("Copy for the import:", copy)
  return copy
end
M.fresh_copy = fresh_copy

local function manual_import(path, lang)
  local _, project, tl = current()
  if not (project and tl) then
    return status(T("no_timeline"))
  end
  S.state = "busy"
  buttons()
  status(T("importing"))
  local page = api(resolve, "GetCurrentPage")
  if page ~= "edit" and page ~= "cut" then
    log("OpenPage edit ->", tostring(api(resolve, "OpenPage", "edit")))
  end
  log("Import SRT:", path, "| language:", lang)
  local source = fresh_copy(path)
  local ok, msg, warning = place_srt(project, tl, source, "WhisperX " .. lang, first_cue_in_file(source),
                                     count_cues(source))
  S.state = "idle"
  buttons()
  status(msg .. (warning and ("\n" .. warning) or ""))
end

local function choose_srt(cb)
  local start = (LAST or "") .. SEP
  if fu then
    local okf, fn = pcall(function() return fu.RequestFile end)
    if okf and type(fn) == "function" then
      local ok, p = pcall(fn, fu, start, "", { FReqB_Saving = false, FReqB_SeqGather = false,
                                               FReqS_Filter = "SRT (*.srt)|*.srt", FReqS_Title = T("choose_srt") })
      if ok then
        if p and p ~= "" then
          local okm, mapped = pcall(function() return fu:MapPath(p) end)
          return cb(okm and mapped and mapped ~= "" and mapped or p)
        end
        return status(T("no_file"))
      end
      log("RequestFile failed:", p)
    end
  end
  ask(T("ask_path"), T("btn_ok"), T("btn_cancel"), function(yes, text)
    if yes and text and text ~= "" then
      cb((tostring(text):gsub('^%s*"?', ""):gsub('"?%s*$', "")))
    else
      status(T("no_file"))
    end
  end, start)
end

function M.import_click()
  if S.state ~= "idle" then
    return
  end
  set_text("Close", T("btn_close"))
  choose_srt(function(path)
    if not file_exists(path) then
      return status(T("file_missing", { path = path }))
    end
    local lang = lang_from_name(path)
    if lang then
      return manual_import(path, lang)
    end
    ask(T("ask_lang"), T("btn_ok"), T("btn_cancel"), function(yes, text)
      local code = clean_lang(text)
      if yes and code then
        manual_import(path, code)
      elseif yes then
        status(T("no_lang"))
      else
        status(T("ready"))
      end
    end, (WX.lang or "en"):upper())
  end)
end

function M.cancel()
  local from = S.state
  if from == "asking" then
    hide_question()
    S.ask_cb = nil
    from = S.ask_from
  end
  if from == "rendering" or S.render_id then
    local project = S.job and S.job.project
    log("StopRendering ->", tostring(api(project, "StopRendering")))
    for _ = 1, 20 do
      if not api(project, "IsRenderingInProgress") then
        break
      end
      wait(0.25)
    end
    restore_render()
  end
  if S.job then
    end_job("cancel.txt", S.done and S.done.files)
  end
  S.state = "idle"
  set_text("Close", T("btn_close"))
  buttons()
  status(T("cancelled"))
end

function M.close()
  if S.job or S.state == "rendering" or S.state == "waiting" then
    M.cancel()
  end
  save_settings()
  if disp then
    disp:ExitLoop()
  end
end

local function guarded(fn)
  return function()
    local ok, err = pcall(fn)
    if not ok then
      log("Error:", err)
      if S.state ~= "waiting" and S.state ~= "rendering" then
        S.state = "idle"
      end
      buttons()
      status(T("error", { why = clean_error(err) }))
    end
  end
end

function M.main()
  log("WhisperX Subtitles", WX.version, "| Lua", tostring(rawget(_G, "_VERSION")),
      G_jit and G_jit.version or "", "| Windows:", tostring(IS_WIN), "| io:", tostring(HAS_IO),
      "| os.remove:", tostring(HAS_REMOVE), "| os.execute:", tostring(HAS_EXECUTE),
      "| ffi:", tostring(get_ffi() ~= nil), "| bmd:", tostring(bmd ~= nil))
  if not (fu and fu.UIManager and bmd and bmd.UIDispatcher) then
    log("No UIManager - start the script from Workspace -> Scripts inside DaVinci Resolve.")
    return
  end
  ui = fu.UIManager
  disp = bmd.UIDispatcher(ui)
  local saved = SETTINGS and read_kv(SETTINGS) or {}
  local list = choices()
  S.choice_lang = list.lang[index_of(list.lang, saved.lang)][1]
  S.choice_mode = list.mode[index_of(list.mode, saved.mode)][1]
  S.choice_filter = saved.filter == "1"
  win = disp:AddWindow({
    ID = "WhisperXWin",
    WindowTitle = T("title") .. " " .. WX.version,
    Geometry = saved_geometry(saved) or { 260, 180, WIN_SIZE[1], WIN_SIZE[2] },
    MinimumSize = WIN_MIN,
    ui:VGroup{
      ID = "Root",
      Spacing = 6,
      ui:Label{ ID = "Info", Text = "", WordWrap = true, Weight = 0, MinimumSize = { 400, 32 } },
      ui:HGroup{                                        -- chosen here, they win over the Workbench window
        Weight = 0,
        ui:Label{ ID = "LangLabel", Text = T("label_lang"), Weight = 0 },
        ui:ComboBox{ ID = "Lang", Weight = 1 },
        ui:Label{ ID = "ModeLabel", Text = T("label_mode"), Weight = 0 },
        ui:ComboBox{ ID = "Mode", Weight = 1 },
        ui:CheckBox{ ID = "Filter", Text = T("label_filter"), Checked = S.choice_filter, Weight = 0 },
      },
      ui:HGroup{
        Weight = 0,
        ui:Button{ ID = "Transcribe", Text = T("btn_transcribe") },
        ui:Button{ ID = "ImportSrt", Text = T("btn_import") },
      },
      -- one label for status, hints and questions: labels shown later lay out badly
      ui:Label{ ID = "Status", Text = "", WordWrap = true, Weight = 1, MinimumSize = { 400, 80 },
                Alignment = { AlignLeft = true, AlignTop = true } },
      ui:HGroup{
        Weight = 0,
        ui:LineEdit{ ID = "Answer", Text = "", Hidden = true },
        ui:Button{ ID = "Yes", Text = T("btn_yes"), Hidden = true },
        ui:Button{ ID = "No", Text = T("btn_no"), Hidden = true },
      },
      ui:HGroup{
        Weight = 0,
        ui:Label{ ID = "Footer", Text = "WhisperX Subtitle Workbench " .. WX.version, Weight = 1,
                  StyleSheet = "QLabel { color: #8a8a8a; }" },
        ui:Button{ ID = "Cancel", Text = T("btn_cancel"), Weight = 0 },
        ui:Button{ ID = "Close", Text = T("btn_close"), Weight = 0 },
      },
    },
  })
  itm = win:GetItems()
  for _, key in ipairs({ "lang", "mode" }) do
    local id = key == "lang" and "Lang" or "Mode"
    for _, item in ipairs(list[key]) do
      pcall(function() itm[id]:AddItem(item[2]) end)
    end
    pcall(function() itm[id].CurrentIndex = index_of(list[key], S["choice_" .. key]) - 1 end)
    win.On[id].CurrentIndexChanged = function()
      local ok, i = pcall(function() return itm[id].CurrentIndex end)
      local item = list[key][(ok and tonumber(i) or 0) + 1]
      S["choice_" .. key] = item and item[1] or ""
      log("Chosen", key, "->", S["choice_" .. key] == "" and "Workbench default" or S["choice_" .. key])
      save_settings()                                 -- kept even if Resolve is closed without "Close"
    end
  end
  win.On.Filter.Clicked = function()
    local ok, checked = pcall(function() return itm.Filter.Checked end)
    if ok and checked ~= nil then
      S.choice_filter = checked and true or false
    end
    log("Chosen filter ->", S.choice_filter and "on" or "off")
    save_settings()
  end

  win.On.WhisperXWin.Close = guarded(M.close)
  win.On.Close.Clicked = guarded(M.close)
  win.On.Transcribe.Clicked = guarded(M.transcribe)
  win.On.ImportSrt.Clicked = guarded(M.import_click)
  win.On.Cancel.Clicked = guarded(M.cancel)
  win.On.Yes.Clicked = guarded(function() answer(true) end)
  win.On.No.Clicked = guarded(function() answer(false) end)

  -- Checks once per second: render progress, done.txt of the Workbench, current timeline.
  -- Both ways of receiving timer events are registered; tick() is safe to call twice.
  local timer = ui:Timer({ ID = "WXTimer", Interval = 1000, SingleShot = false })
  disp.On.Timeout = function(ev)
    if ev == nil or ev.who == nil or ev.who == "WXTimer" then
      tick()
    end
  end
  pcall(function()
    disp.On.WXTimer.Timeout = function() tick() end
  end)

  S.can_import = true
  S.can_transcribe = true
  if not WX.workbench then
    S.can_transcribe, S.can_import = false, false
    status(T("not_installed"))
  elseif not HAS_IO then
    S.can_transcribe = false
    status(T("no_io"))
  elseif not file_exists(WX.python) or not file_exists(join(WX.workbench, "whisper_tool.py")) then
    S.can_transcribe = false
    status(T("no_workbench", { path = WX.workbench }))
  else
    status(T("ready"))
  end
  if WX.workbench then
    refresh_info()
  end
  buttons()
  timer:Start()
  win:Show()
  relayout()                                          -- the first layout is made for a wrong size
  disp:RunLoop()
  timer:Stop()
  win:Hide()
  log("Window closed")
end

if rawget(_G, "WX_TEST") then
  return M
end
M.main()
