-- ChatCC installer. The server injects its own URL in place of {{SERVER}}
-- when this file is served from /install, so the client only ever talks to it.
local SERVER = "{{SERVER}}"

print("Installing ChatCC client from " .. SERVER)

local resp, err = http.get(SERVER .. "/client")
if not resp then
    error("Could not download client: " .. tostring(err), 0)
end
local code = resp.readAll()
resp.close()

if not code or #code < 10 then
    error("Downloaded client looks empty", 0)
end

local f = fs.open("ytchat", "w")
f.write(code)
f.close()

print("Installed 'ytchat'.")
print("Run:  ytchat <youtube-live-url-or-id>")
