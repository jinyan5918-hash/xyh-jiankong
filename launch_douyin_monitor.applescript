on run
	set projectDir to "/Users/admin/Documents/jiankong"
	set cmd to "cd " & quoted form of projectDir & "; " & ¬
		"if [ ! -f config.json ]; then cp config.example.json config.json; fi; " & ¬
		". .venv/bin/activate; " & ¬
		"python -u douyin_monitor_gui.py"
	tell application "Terminal"
		activate
		do script cmd
	end tell
end run
