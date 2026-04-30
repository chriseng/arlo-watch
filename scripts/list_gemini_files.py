
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
files = list(client.files.list())
print(f'{len(files)} file(s) in Gemini Files API:')
for f in files:
    print(f'  {f.name}  state={f.state.name}  created={f.create_time}')
