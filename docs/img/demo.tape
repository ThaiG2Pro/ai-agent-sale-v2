# Re-render: start the API (see README Quickstart), then `vhs docs/img/demo.tape`
Output docs/img/demo.gif
Set Shell bash
Set FontSize 15
Set Width 1100
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Padding 16

Hide
Type "export API_URL=${API_URL:-http://localhost:8000}; clear" Enter
Show

Type "python3 scripts/demo_ask.py 'Điện thoại nào pin tốt dưới 10 triệu?'" Enter
Sleep 14s
Type "clear" Enter
Type "python3 scripts/demo_ask.py 'POCO F6 giá bao nhiêu, còn hàng không?'" Enter
Sleep 12s
Sleep 3s
