@echo off
rem Starts Portable AI in LAN mode: reachable from phones/tablets on the same network.
rem The access token is printed in the console and in Settings -> LAN access.
call "%~dp0Start-AI.cmd" --lan
