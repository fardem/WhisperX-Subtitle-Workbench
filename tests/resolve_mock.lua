-- resolve_mock.lua - a small fake of DaVinci Resolve for tests/test_resolve_plugin.py
--
-- It imitates only what the plugin uses, with the behaviour tested in Resolve Studio 21.1.2
-- (resolve/tools/probe_subtitles.lua): an SRT is appended to the active subtitle track only,
-- on an empty track it starts at the timeline start, new tracks are inactive, nothing is
-- placed when no track is active, a locked track takes nothing.
-- File system access goes through PY.* functions handed in by the Python test.

local MOCK = { calls = {}, page = "edit", renders = {}, presets = { "Default" }, next_job = 1,
               settings = {}, format = { format = "mov", codec = "H264" }, render_mode = 0,
               saved = 0, playing = false, version = "21.1.2.10" }
_G.MOCK = MOCK

local function record(name, ...)
  MOCK.calls[#MOCK.calls + 1] = { name, ... }
end

local function method_table(t)
  return setmetatable(t, { __index = function(_, k)
    error("mock: method " .. tostring(k) .. " is not implemented", 2)
  end })
end

-- ─── Timeline ────────────────────────────────────────────────────────────────

local function new_timeline(opts)
  local tl = { name = opts.name or "Timeline 1", id = opts.id or "TL-1", fps = opts.fps or 25,
               fps_text = opts.fps_text, drop = opts.drop or "0", start = opts.start or 0,
               start_tc = opts.start_tc or "00:00:00:00", last = opts.last or 1000, marks = opts.marks or {},
               tracks = {}, tc = "00:00:10:00" }
  for i, t in ipairs(opts.tracks or {}) do
    tl.tracks[i] = { name = t.name or ("Subtitle " .. i), enabled = t.enabled or false,
                     locked = t.locked or false, items = t.items or {} }
  end
  local T = {}
  function T:GetName() return tl.name end
  function T:GetUniqueId() return tl.id end
  function T:GetSetting(key)
    if key == "timelineFrameRate" then return tl.fps_text or tl.fps end
    if key == "timelineDropFrameTimecode" then return tl.drop end
    return ""
  end
  function T:GetStartFrame() return tl.start end
  function T:GetEndFrame() return tl.last end
  function T:GetStartTimecode() return tl.start_tc end
  function T:GetCurrentTimecode()
    if MOCK.playing then
      MOCK.frame = (MOCK.frame or 0) + 1
      return "00:00:" .. MOCK.frame
    end
    return tl.tc
  end
  function T:GetMarkInOut()
    local out = {}
    for k, m in pairs(tl.marks) do out[k] = { ["in"] = m["in"], out = m.out } end
    return out
  end
  function T:SetMarkInOut(a, b, kind)
    record("SetMarkInOut", a, b, kind)
    kind = kind or "all"
    for _, k in ipairs(kind == "all" and { "video", "audio" } or { kind }) do
      tl.marks[k] = { ["in"] = a, out = b }
    end
    return true
  end
  function T:ClearMarkInOut(kind)
    record("ClearMarkInOut", kind)
    kind = kind or "all"
    for _, k in ipairs(kind == "all" and { "video", "audio" } or { kind }) do tl.marks[k] = nil end
    return true
  end
  function T:GetTrackCount(kind)
    if kind == "subtitle" then return #tl.tracks end
    return 1
  end
  function T:GetItemListInTrack(kind, i)
    local t = tl.tracks[i]
    if kind ~= "subtitle" or not t then return nil end
    local out = {}
    for j, it in ipairs(t.items) do
      out[j] = method_table({ GetStart = function() return it.start end, GetName = function() return it.text end })
    end
    if #out == 0 then out.dummy = true end         -- pairs() would count this - # does not
    return out
  end
  function T:AddTrack(kind, opts2)
    record("AddTrack", kind)
    if kind ~= "subtitle" then return false end
    local n = #tl.tracks + 1
    table.insert(tl.tracks, { name = "Subtitle " .. n, enabled = false, locked = false, items = {} })
    return true
  end
  function T:DeleteTrack(kind, i)
    record("DeleteTrack", kind, i)
    table.remove(tl.tracks, i)
    return true
  end
  function T:SetTrackEnable(kind, i, on)
    record("SetTrackEnable", kind, i, on)
    local t = tl.tracks[i]
    if kind ~= "subtitle" or not t then return false end
    if on then
      for _, other in ipairs(tl.tracks) do other.enabled = false end    -- only one active subtitle track
    end
    t.enabled = on and true or false
    return true
  end
  function T:GetIsTrackEnabled(kind, i) return tl.tracks[i] and tl.tracks[i].enabled or false end
  function T:GetIsTrackLocked(kind, i) return tl.tracks[i] and tl.tracks[i].locked or false end
  function T:GetTrackName(kind, i) return tl.tracks[i] and tl.tracks[i].name or "" end
  function T:SetTrackName(kind, i, name)
    record("SetTrackName", kind, i, name)
    if not tl.tracks[i] then return false end
    tl.tracks[i].name = name
    return true
  end
  tl.api = method_table(T)
  return tl
end
MOCK.new_timeline = new_timeline

-- ─── Media pool ──────────────────────────────────────────────────────────────

local function new_folder(name)
  local f = { name = name, subs = {}, clips = {} }
  f.api = method_table({
    GetName = function() return f.name end,
    GetSubFolderList = function()
      local out = {}
      for i, s in ipairs(f.subs) do out[i] = s.api end
      return out
    end,
  })
  return f
end

local function parse_srt_starts(path)
  local text = PY.read(path)
  local starts = {}
  if text then
    for h, m, s, ms in text:gmatch("(%d+):(%d+):(%d+)[,.](%d+)%s*%-%->") do
      starts[#starts + 1] = (tonumber(h) * 60 + tonumber(m)) * 60 + tonumber(s) + tonumber(ms) / 1000
    end
  end
  return starts
end

MOCK.root = new_folder("Master")
MOCK.current_folder = MOCK.root

local MP = {}
function MP:GetRootFolder() return MOCK.root.api end
function MP:GetCurrentFolder() return MOCK.current_folder.api end
function MP:SetCurrentFolder(api)
  local function find(f)
    if f.api == api then return f end
    for _, s in ipairs(f.subs) do
      local r = find(s)
      if r then return r end
    end
  end
  local f = find(MOCK.root)
  if f then MOCK.current_folder = f end
  record("SetCurrentFolder", f and f.name)
  return f ~= nil
end
function MP:AddSubFolder(parent, name)
  record("AddSubFolder", name)
  local f = new_folder(name)
  table.insert(MOCK.root.subs, f)
  MOCK.current_folder = f
  return f.api
end
function MP:ImportMedia(list)
  record("ImportMedia", list[1], MOCK.current_folder.name)
  if MOCK.import_fails or not PY.exists(list[1]) then return {} end
  -- like Resolve: a path that is already in the media pool is not read again - the clip of the
  -- first import comes back, with the subtitles it had then
  local known = MOCK.by_path[list[1]]
  if known then
    MOCK.imported = known
    return { known.api }
  end
  local item = { path = list[1], folder = MOCK.current_folder.name, starts = parse_srt_starts(list[1]) }
  MOCK.by_path[list[1]] = item
  table.insert(MOCK.current_folder.clips, item)
  item.api = method_table({
    GetClipProperty = function() return { Type = "Subtitle", ["File Path"] = item.path } end,
    GetName = function() return item.path end,
  })
  MOCK.imported = item
  return { item.api }
end
function MP:AppendToTimeline(list)
  record("AppendToTimeline", #list)
  local tl = MOCK.timeline
  local item = MOCK.imported
  local target
  for _, t in ipairs(tl.tracks) do
    if t.enabled then target = t end
  end
  if not target or target.locked or not item then
    return {}                                      -- truthy, but nothing placed
  end
  local base = tl.start
  if #target.items > 0 then
    base = target.items[#target.items].stop
  end
  local fps = tl.fps
  local out = {}
  for i, s in ipairs(item.starts) do
    if MOCK.max_place and i > MOCK.max_place then break end
    local start = base + math.floor(s * fps + 0.5) + (MOCK.placement_shift or 0)
    table.insert(target.items, { start = start, stop = start + 10, text = "cue" })
    out[#out + 1] = {}
  end
  return out
end

-- ─── Project ─────────────────────────────────────────────────────────────────

local P = {}
function P:GetName() return "Test project" end
function P:GetCurrentTimeline() return MOCK.timeline and MOCK.timeline.api or nil end
function P:GetTimelineCount() return #MOCK.timelines end
function P:GetTimelineByIndex(i) return MOCK.timelines[i] and MOCK.timelines[i].api end
function P:SetCurrentTimeline(api)
  record("SetCurrentTimeline")
  for _, t in ipairs(MOCK.timelines) do
    if t.api == api then MOCK.timeline = t return true end
  end
  return false
end
function P:GetMediaPool() return MOCK.mp end
function P:IsRenderingInProgress()
  local r = MOCK.active_render
  return r ~= nil and r.ticks_left > 0
end
function P:GetRenderJobList()
  local out = {}
  for _, r in ipairs(MOCK.renders) do
    out[#out + 1] = { JobId = r.id, TargetDir = r.settings.TargetDir, OutputFilename = r.name,
                      MarkIn = r.settings.MarkIn, MarkOut = r.settings.MarkOut }
  end
  return out
end
function P:GetRenderJobStatus(id)
  for _, r in ipairs(MOCK.renders) do
    if r.id == id then
      return { JobStatus = r.status, CompletionPercentage = r.pct, Error = r.error }
    end
  end
  return {}
end
function P:DeleteRenderJob(id)
  record("DeleteRenderJob", id)
  for i, r in ipairs(MOCK.renders) do
    if r.id == id then table.remove(MOCK.renders, i) return true end
  end
  return false
end
function P:AddRenderJob()
  MOCK.page = "deliver"
  local s = {}
  for k, v in pairs(MOCK.settings) do s[k] = v end
  local id = "job-" .. MOCK.next_job
  MOCK.next_job = MOCK.next_job + 1
  local r = { id = id, settings = s, name = (s.CustomName or "x") .. ".wav", pct = 0, status = "Ready",
              error = "", ticks_left = 0 }
  table.insert(MOCK.renders, r)
  record("AddRenderJob", id)
  return id
end
function P:StartRendering(id)
  record("StartRendering", id)
  if MOCK.refuse_start and MOCK.refuse_start > 0 then
    MOCK.refuse_start = MOCK.refuse_start - 1
    return false
  end
  for _, r in ipairs(MOCK.renders) do
    if r.id == id then
      r.ticks_left = MOCK.render_ticks or 2
      r.status = "Rendering"
      MOCK.active_render = r
      return true
    end
  end
  return false
end
function P:StopRendering()
  record("StopRendering")
  if MOCK.active_render then
    MOCK.active_render.ticks_left = 0
    MOCK.active_render.status = "Cancelled"
    MOCK.active_render = nil
  end
end
function P:SaveAsNewRenderPreset(name)
  record("SaveAsNewRenderPreset", name)
  table.insert(MOCK.presets, name)
  MOCK.preset_store = MOCK.preset_store or {}
  local copy = {}
  for k, v in pairs(MOCK.settings) do copy[k] = v end
  MOCK.preset_store[name] = { settings = copy, format = MOCK.format.format, codec = MOCK.format.codec }
  return true
end
function P:LoadRenderPreset(name)
  record("LoadRenderPreset", name)
  local p = (MOCK.preset_store or {})[name]
  if not p then return false end
  MOCK.settings = {}
  for k, v in pairs(p.settings) do MOCK.settings[k] = v end
  MOCK.format = { format = p.format, codec = p.codec }
  return true
end
function P:DeleteRenderPreset(name)
  record("DeleteRenderPreset", name)
  for i, p in ipairs(MOCK.presets) do
    if p == name then table.remove(MOCK.presets, i) return true end
  end
  return false
end
function P:GetRenderPresetList()
  local out = {}
  for i, p in ipairs(MOCK.presets) do out[i] = p end
  return out
end
function P:GetRenderFormats() return { wav = "wav", mov = "mov", mp4 = "mp4" } end
function P:GetRenderCodecs(fmt)
  if fmt == "wav" then return { ["Linear PCM"] = "LinearPCM" } end
  return {}
end
function P:SetCurrentRenderFormatAndCodec(fmt, codec)
  record("SetCurrentRenderFormatAndCodec", fmt, codec)
  if MOCK.no_wav and fmt == "wav" then return false end
  MOCK.format = { format = fmt, codec = codec }
  return true
end
function P:GetCurrentRenderFormatAndCodec() return { format = MOCK.format.format, codec = MOCK.format.codec } end
function P:GetCurrentRenderMode()
  MOCK.page = "deliver"                            -- the real getter switches to Deliver
  return MOCK.render_mode
end
function P:SetCurrentRenderMode(m) MOCK.render_mode = m return true end
function P:SetRenderSettings(s)
  local known = { SelectAllFrames = 1, MarkIn = 1, MarkOut = 1, TargetDir = 1, CustomName = 1, ExportVideo = 1,
                  ExportAudio = 1, AudioSampleRate = 1, AudioBitDepth = 1 }
  for k, v in pairs(s) do
    record("SetRenderSettings", k, v)
    if MOCK.reject_setting == k then return false end
    if known[k] then MOCK.settings[k] = v end      -- unknown keys are ignored silently
    if k == "MarkIn" or k == "MarkOut" then        -- the Deliver page writes the timeline marks
      local tl = MOCK.timeline
      local rel = v - tl.start
      for _, kind in ipairs({ "video", "audio" }) do
        tl.marks[kind] = tl.marks[kind] or {}
        tl.marks[kind][k == "MarkIn" and "in" or "out"] = rel
      end
    end
  end
  return true
end

local PM = {}
function PM:GetCurrentProject() return MOCK.project_api end
function PM:SaveProject() MOCK.saved = MOCK.saved + 1 return true end

-- render progress, driven by the test (one step per timer tick)
function MOCK.advance_render()
  local r = MOCK.active_render
  if not r then return end
  if r.ticks_left > 0 then
    r.ticks_left = r.ticks_left - 1
    r.pct = math.min(100, r.pct + 50)
  end
  if r.ticks_left == 0 then
    if MOCK.render_error then
      r.error, r.status = MOCK.render_error, "Failed"
    else
      r.pct, r.status = 100, "Complete"
      local dir = r.settings.TargetDir
      if dir and not MOCK.no_output then
        PY.write(dir .. PY.sep .. r.name, "RIFF....WAVEfmt fake audio")
      end
    end
    MOCK.active_render = nil
  end
end

-- ─── Resolve, Fusion, bmd, UIManager ─────────────────────────────────────────

local R = {}
function R:GetProjectManager() return MOCK.pm end
function R:GetCurrentPage() return MOCK.page end
function R:OpenPage(p)
  record("OpenPage", p)
  MOCK.page = p
  if p == "deliver" and MOCK.deliver_marks then        -- the Deliver page may overwrite In/Out
    MOCK.timeline.marks = { video = MOCK.deliver_marks, audio = MOCK.deliver_marks }
  end
  return true
end
function R:GetVersionString() return MOCK.version end
function R:GetProductName() return "DaVinci Resolve Studio" end

local function auto_table()
  return setmetatable({}, { __index = function(t, k)
    local v = {}
    rawset(t, k, v)
    return v
  end })
end

local UI = {}
local function widget(kind)
  return function(self, spec)
    spec = spec or {}
    spec.kind = kind
    if kind == "ComboBox" then
      spec.entries, spec.CurrentIndex = {}, 0
      function spec:AddItem(text) self.entries[#self.entries + 1] = text end
    end
    return spec
  end
end
for _, kind in ipairs({ "VGroup", "HGroup", "Label", "Button", "LineEdit", "TextEdit", "ComboBox", "CheckBox",
                        "SpinBox" }) do
  UI[kind] = widget(kind)
end
local spin_box = UI.SpinBox
function UI:SpinBox(spec)
  assert(not MOCK.no_spinbox, "unknown element SpinBox")      -- an older UIManager
  return spin_box(self, spec)
end
function UI:VGap() return { kind = "Gap" } end
function UI:HGap() return { kind = "Gap" } end
function UI:Timer(spec)
  local t = { ID = spec.ID, Interval = spec.Interval, running = false }
  function t:Start() t.running = true end
  function t:Stop() t.running = false end
  MOCK.timer = t
  return t
end

local function collect(spec, items)
  if type(spec) ~= "table" then return end
  if spec.ID then items[spec.ID] = spec end
  for _, child in ipairs(spec) do collect(child, items) end
end

local disp = { On = auto_table() }
function disp:AddWindow(spec)
  local items = {}
  for _, child in ipairs(spec) do collect(child, items) end
  items[spec.ID] = { Geometry = MOCK.move_to or spec.Geometry }  -- the window itself is an item too
  MOCK.window_spec = spec
  local win = { On = auto_table(), spec = spec, shown = false }
  function win:GetItems() return items end
  function win:Show() win.shown = true end
  function win:Hide() win.shown = false end
  function win:RecalcLayout() record("RecalcLayout") end
  function win:Raise() record("Raise") end
  function win:ActivateWindow() record("ActivateWindow") end
  MOCK.win, MOCK.items = win, items
  return win
end
function disp:RunLoop()
  MOCK.loop = true
  PY.scenario()
  MOCK.loop = false
end
function disp:ExitLoop() MOCK.exited = true end
MOCK.disp = disp

-- helpers for the test
function MOCK.click(id)
  local h = MOCK.win.On[id] and MOCK.win.On[id].Clicked
  assert(h, "no handler for " .. id)
  h({ who = id })
end
function MOCK.tick()
  MOCK.advance_render()
  local done = false
  if rawget(disp.On, "Timeout") then disp.On.Timeout({ who = "WXTimer" }) done = true end
  local by_id = rawget(disp.On, "WXTimer")
  if by_id and by_id.Timeout then by_id.Timeout({ who = "WXTimer" }) done = true end
  assert(done, "no timer handler")
end
function MOCK.text(id) return MOCK.items[id].Text end
function MOCK.choose(id, index)
  MOCK.items[id].CurrentIndex = index
  local h = MOCK.win.On[id] and MOCK.win.On[id].CurrentIndexChanged
  if h then h({}) end
end
function MOCK.set_number(id, value)
  local item = MOCK.items[id]
  local event = item.kind == "SpinBox" and "ValueChanged" or "TextChanged"
  if item.kind == "SpinBox" then item.Value = value else item.Text = value end
  local h = MOCK.win.On[id] and MOCK.win.On[id][event]
  if h then h({}) end
end
function MOCK.check(id, checked)
  MOCK.items[id].Checked = checked
  local h = MOCK.win.On[id] and MOCK.win.On[id].Clicked
  if h then h({ who = id }) end
end
function MOCK.close_window() MOCK.win.On.WhisperXWin.Close({}) end

function MOCK.setup(opts)
  MOCK.timelines = {}
  MOCK.by_path = {}
  for i, o in ipairs(opts.timelines) do MOCK.timelines[i] = new_timeline(o) end
  MOCK.timeline = MOCK.timelines[opts.current or 1]
  MOCK.mp = method_table(MP)
  MOCK.project_api = method_table(P)
  MOCK.pm = method_table(PM)
  _G.resolve = method_table(R)
  local FU = { UIManager = UI }
  function FU:RequestFile(path, name, o)
    record("RequestFile", path, o and o.FReqS_Filter)
    return MOCK.request_file
  end
  function FU:MapPath(p) return p end
  _G.fu = FU
  _G.bmd = {
    UIDispatcher = function() return disp end,
    wait = function() end,
    fileexists = function(p) return PY.exists(p) end,
    direxists = function(p) return PY.isdir(p) end,
    createdir = function(p) return PY.mkdir(p) end,
    removedir = function(p) return PY.rmdir(p) end,
  }
end
