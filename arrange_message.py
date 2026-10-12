text = ""
new_text = ""
with open("message_history.txt", "r", encoding="utf-8") as file:
    text = file.read()
start = 0
for i in range(len(text)):
    if text[i] == "," or text[i] == "{" or text[i] == "{":
        new_text = new_text + text[start:i] + "\n"
        start = i
with open("message_history.txt", "w", encoding="utf-8") as f:
    f.write(str(new_text))
