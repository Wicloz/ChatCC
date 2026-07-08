-- ChatCC client ('ytchat'): read a YouTube live chat on a CC tablet.
-- The server injects its own URL in place of {{SERVER}} when serving /client.
local SERVER = "{{SERVER}}"

local args = { ... }
if #args < 1 then
    print("Usage: ytchat <youtube-live-url-or-id>")
    print("       ytchat login")
    print("       ytchat logout [all]")
    return
end
local video = args[1]

local TOKEN_FILE = ".ytchat-token"

-- Derive the WebSocket URL from the injected HTTP base (https->wss, http->ws).
local WS_BASE = SERVER:gsub("^http", "ws")

-- CC terminals have no Unicode support: a Lua string is raw bytes, one byte
-- is one glyph from a fixed 256-glyph font. textutils.unserialiseJSON always
-- hands back a valid *UTF-8* string, though (confirmed CC:Tweaked behaviour,
-- not a bug we can configure away) -- so a character CC could render natively
-- as a single byte (e.g. é "e") arrives re-expanded into a multi-byte
-- UTF-8 sequence, which prints as multiple wrong glyphs.
--
-- The server already guarantees every character it sends is either plain
-- ASCII or Latin-1 Supplement (both <= codepoint 255; CC's font matches
-- ISO-8859-1 in that range) or a bounded '?' run, so every codepoint we see
-- here is guaranteed <= 255. Collapsing each back to a single byte is always
-- safe and total -- this undoes exactly the UTF-8 re-expansion above.
local function toCCText(s)
    local out = {}
    for _, cp in utf8.codes(s) do
        out[#out + 1] = (cp <= 255) and string.char(cp) or "?"
    end
    return table.concat(out)
end

-- CC fires a 'char' event immediately after a 'key' event for any
-- letter/digit/symbol keypress. Our shortcuts 't' (start composing) and 'q'
-- (quit/cancel) are both letters, so their paired 'char' event arrives right
-- behind the 'key' event we already acted on -- and leaks into whatever reads
-- the next 'char' event: the compose box if we just entered it, or the
-- shell's prompt if we just exited the program. Swallow that one paired event
-- so it never reaches either. (Enter/Backspace/arrows have no paired char
-- event, so this is only needed for letter-key shortcuts.)
local function swallowPairedChar()
    local timer = os.startTimer(0)
    while true do
        local ev = { os.pullEvent() }
        if ev[1] == "char" then
            return
        elseif ev[1] == "timer" and ev[2] == timer then
            return
        else
            -- Not the event we're waiting for (e.g. an incoming chat message
            -- landed in this brief window) -- put it back, don't drop it.
            os.queueEvent(table.unpack(ev))
        end
    end
end

local function urlencode(s)
    return (s:gsub("[^%w%-%._~]", function(c)
        return string.format("%%%02X", string.byte(c))
    end))
end

-- OAuth device-flow login. The tablet only shows a code and stores the bearer
-- token the server hands back; it never sees Google or any secret.
local function runLogin()
    term.clear()
    term.setCursorPos(1, 1)
    print("Connecting...")
    local ws, err = http.websocket(WS_BASE .. "/ws/login")
    if not ws then
        printError("Connection failed: " .. tostring(err))
        return
    end
    while true do
        local ev = { os.pullEvent() }
        local name = ev[1]
        if name == "websocket_message" then
            local raw = ev[3]
            local op = raw:sub(1, 1)
            local data = textutils.unserialiseJSON(raw:sub(2))
            if op == "D" and data then
                term.clear()
                term.setCursorPos(1, 1)
                print("To log in with your Google account:")
                print("")
                print("1. On your phone or PC, open:")
                print("   " .. (data.url or "google.com/device"))
                print("")
                print("2. Enter this code:")
                term.setTextColor(colors.yellow)
                print("")
                print("   " .. (data.code or "?"))
                term.setTextColor(colors.white)
                print("")
                print("Waiting for approval... (q to cancel)")
            elseif op == "A" and data then
                local f = fs.open(TOKEN_FILE, "w")
                f.write(data.token)
                f.close()
                term.clear()
                term.setCursorPos(1, 1)
                local who = (data.account and data.account ~= "") and (" as " .. toCCText(data.account)) or ""
                print("Logged in" .. who .. ".")
                print("You can now send messages while watching chat.")
                pcall(function() ws.close() end)
                return
            elseif op == "S" and data and data.s == "error" then
                printError("Login failed: " .. (data.m or "unknown error"))
                pcall(function() ws.close() end)
                return
            end
        elseif name == "websocket_closed" then
            printError("Connection closed.")
            return
        elseif name == "key" and ev[2] == keys.q then
            swallowPairedChar()
            pcall(function() ws.close() end)
            print("Cancelled.")
            return
        end
    end
end

if video == "login" then
    runLogin()
    return
end

-- Log out: revoke this computer's token, or every device for the account ("all").
-- The server-side revoke is what matters, so only delete the local token once
-- the server confirms; on failure keep it so the user can retry.
local function runLogout(all)
    if not fs.exists(TOKEN_FILE) then
        print("Not logged in on this computer.")
        if all then
            print("Run 'logout all' from a computer that is still logged in.")
        end
        return
    end
    local f = fs.open(TOKEN_FILE, "r")
    local token = f.readAll()
    f.close()

    local body = textutils.serialiseJSON({ token = token, all = all and true or false })
    local resp, err = http.post(SERVER .. "/logout", body, { ["Content-Type"] = "application/json" })
    if not resp then
        printError("Could not reach server: " .. tostring(err))
        print("Still logged in. Try again when online.")
        return
    end
    local data = textutils.unserialiseJSON(resp.readAll())
    resp.close()
    fs.delete(TOKEN_FILE)

    local n = (data and data.revoked) or 0
    if all then
        print("Logged out " .. n .. " device(s) for your account.")
    else
        print("Logged out this computer.")
    end
end

if video == "logout" then
    runLogout(args[2] == "all")
    return
end

local WS_URL = WS_BASE .. "/ws/chat?v=" .. urlencode(video)

local ROLE_COLOR = {
    owner = colors.orange,
    moderator = colors.blue,
    member = colors.lime,
    verified = colors.lightGray,
    user = colors.yellow,
}
local INDENT = "  "

-- State -------------------------------------------------------------------
local messages = {}   -- {author=, text=, role=}
local lines = {}      -- flattened display lines; each line = {segments {c=,t=}}
local scroll = 0      -- lines scrolled up from the bottom (0 = newest)
local statusText = "connecting..."
local W, H = term.getSize()

-- Sending: load the bearer token saved by `ytchat login`, if present.
local authToken = nil
if fs.exists(TOKEN_FILE) then
    local f = fs.open(TOKEN_FILE, "r")
    authToken = f.readAll()
    f.close()
end
local composing = false
local input = ""
local MAX_INPUT = 200   -- YouTube live chat message limit

-- Wrap one message into colored display lines with a hanging indent.
local function messageToLines(msg, width)
    local out = {}
    local cur, curw = {}, 0
    local function newline(indent)
        out[#out + 1] = cur
        cur, curw = {}, 0
        if indent then
            cur[#cur + 1] = { c = colors.gray, t = INDENT }
            curw = #INDENT
        end
    end
    local function addWord(word, color)
        if curw > 0 and curw + #word > width then
            newline(true)
        end
        while #word > width - curw do          -- word longer than a line: hard split
            local take = math.max(1, width - curw)
            cur[#cur + 1] = { c = color, t = word:sub(1, take) }
            word = word:sub(take + 1)
            newline(true)
        end
        cur[#cur + 1] = { c = color, t = word }
        curw = curw + #word
    end

    -- System events (memberships, super chats, etc.) already name the user in
    -- their text, so render them as a compact dim line with no author prefix
    -- instead of wasting space repeating the name.
    if msg.mtype and msg.mtype ~= "textMessageEvent" then
        addWord(">", colors.gray)
        for word in (msg.text or ""):gmatch("%S+") do
            if curw + 1 <= width then
                cur[#cur + 1] = { c = colors.gray, t = " " }
                curw = curw + 1
            end
            addWord(word, colors.gray)
        end
        newline(false)
        return out
    end

    addWord((msg.author or "?") .. ":", ROLE_COLOR[msg.role] or colors.white)
    for word in (msg.text or ""):gmatch("%S+") do
        if curw + 1 <= width then
            cur[#cur + 1] = { c = colors.white, t = " " }
            curw = curw + 1
        end
        addWord(word, colors.white)
    end
    newline(false)
    return out
end

local function rebuildLines()
    lines = {}
    for _, m in ipairs(messages) do
        for _, l in ipairs(messageToLines(m, W)) do
            lines[#lines + 1] = l
        end
    end
end

local function addMessage(m)
    messages[#messages + 1] = m
    local added = messageToLines(m, W)
    for _, l in ipairs(added) do
        lines[#lines + 1] = l
    end
    -- If the user has scrolled up, keep their view anchored as lines arrive.
    if scroll > 0 then
        scroll = scroll + #added
    end
end

local function redraw()
    W, H = term.getSize()
    local areaH = H - 1   -- everything except the status bar is chat
    term.setBackgroundColor(colors.black)
    term.clear()

    -- Chat area
    local total = #lines
    local maxScroll = math.max(0, total - areaH)
    if scroll > maxScroll then scroll = maxScroll end
    local startIdx = math.max(1, total - areaH + 1 - scroll)
    local endIdx = math.min(total, startIdx + areaH - 1)
    local row = 1
    for i = startIdx, endIdx do
        term.setCursorPos(1, row)
        for _, seg in ipairs(lines[i]) do
            term.setTextColor(seg.c)
            term.write(seg.t)
        end
        row = row + 1
    end

    -- Bottom line: a compose box while typing, otherwise the status bar.
    term.setCursorPos(1, H)
    if composing then
        term.setBackgroundColor(colors.black)
        term.setTextColor(colors.white)
        term.clearLine()
        local prompt = "> "
        local shown = input
        -- Scroll the field so the caret stays visible on a narrow screen.
        if #prompt + #shown > W - 1 then
            shown = shown:sub(#prompt + #shown - (W - 1) + 1)
        end
        term.write(prompt .. shown)
        term.setCursorBlink(true)
    else
        term.setBackgroundColor(colors.gray)
        term.setTextColor(colors.white)
        term.clearLine()
        local hint = scroll > 0 and ("[+" .. scroll .. "] ") or ""
        local act = authToken and "t:send" or "login req'd"
        term.write((" " .. statusText .. "  " .. hint .. act .. " q:quit"):sub(1, W))
        term.setCursorBlink(false)
    end
    term.setBackgroundColor(colors.black)
end

local function setStatus(s) statusText = s end

local function handleFrame(raw)
    local op = raw:sub(1, 1)
    local data = textutils.unserialiseJSON(raw:sub(2))
    if not data then return end
    if op == "M" then
        addMessage({ author = toCCText(data.a), text = toCCText(data.m), role = data.r, mtype = data.t })
        redraw()
    elseif op == "S" then
        local s = data.s
        if s == "connecting" then setStatus("connecting...")
        elseif s == "live" then setStatus("LIVE")
        elseif s == "ended" then setStatus("stream ended")
        elseif s == "error" then setStatus("error: " .. (data.m or "?"))
        else setStatus(s) end
        redraw()
    end
end

-- Connect + event loop ----------------------------------------------------
local function run()
    local attempt = 0
    while true do
        setStatus("connecting...")
        redraw()
        local ws, err = http.websocket(WS_URL)
        if not ws then
            attempt = attempt + 1
            if attempt > 5 then
                setStatus("could not connect: " .. tostring(err))
                redraw()
                return
            end
            setStatus("connect failed, retrying (" .. attempt .. ")...")
            redraw()
            sleep(2)
        else
            attempt = 0
            local quit = false
            while true do
                local ev = { os.pullEvent() }
                local name = ev[1]
                if name == "websocket_message" then
                    handleFrame(ev[3])
                elseif name == "websocket_closed" then
                    setStatus("disconnected, reconnecting...")
                    composing = false
                    redraw()
                    break
                elseif name == "char" then
                    if composing and #input < MAX_INPUT then
                        input = input .. ev[2]
                        redraw()
                    end
                elseif name == "paste" then
                    if composing then
                        input = (input .. ev[2]):sub(1, MAX_INPUT)
                        redraw()
                    end
                elseif name == "key" then
                    local k = ev[2]
                    if composing then
                        if k == keys.enter then
                            local text = input
                            input, composing = "", false
                            if #text > 0 then
                                ws.send("P" .. textutils.serialiseJSON({ m = text, k = authToken }))
                            end
                            redraw()
                        elseif k == keys.backspace then
                            input = input:sub(1, #input - 1)
                            redraw()
                        end
                    elseif k == keys.q then
                        swallowPairedChar()
                        quit = true; break
                    elseif k == keys.enter or k == keys.t then
                        if authToken then
                            composing = true
                            if k == keys.t then swallowPairedChar() end
                        else
                            setStatus("not logged in - run: ytchat login")
                        end
                        redraw()
                    elseif k == keys.pageUp then
                        scroll = scroll + (H - 2); redraw()
                    elseif k == keys.pageDown then
                        scroll = math.max(0, scroll - (H - 2)); redraw()
                    elseif k == keys.up then
                        scroll = scroll + 1; redraw()
                    elseif k == keys.down then
                        scroll = math.max(0, scroll - 1); redraw()
                    elseif k == keys["end"] then
                        scroll = 0; redraw()
                    end
                elseif name == "term_resize" then
                    rebuildLines(); redraw()
                end
            end
            pcall(function() ws.close() end)
            if quit then return end
            sleep(1)
        end
    end
end

run()
term.setCursorBlink(false)
term.setBackgroundColor(colors.black)
term.setTextColor(colors.white)
term.clear()
term.setCursorPos(1, 1)
print("ytchat closed.")
