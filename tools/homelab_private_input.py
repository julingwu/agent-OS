"""AgentOS native entry: only session ID in the launcher; secrets stay in helper stdin/UI."""
import json,re,sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox,simpledialog
sys.path.insert(0,str(Path('D:/Agent-OS')))
from app import homelab_transport as transport

def main():
 window=tk.Tk();window.withdraw()
 session=simpledialog.askstring('AgentOS private credential input','Current AgentOS session ID (nonsecret):',parent=window)
 if session is None:return
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',session):
  messagebox.showerror('AgentOS','Invalid session ID.',parent=window);return
 try:
  cfg=transport.config()
  result=transport.invoke([cfg['python'],'-I',str(Path(cfg['directory'])/'homelab_message_capture.py')],{'action':'private_input','session_id':session},timeout=300)
 except Exception:
  messagebox.showerror('AgentOS','private_capture_failed',parent=window);return
 if result.get('cancelled'):return
 # The trusted helper returns only sanitized context and encrypted-receipt metadata.
 window.deiconify();window.title('AgentOS encrypted receipt — not yet saved to Vaultwarden')
 tk.Label(window,text='Send this receipt to the current AgentOS session. Save requires readback_verified=true.').pack(padx=12,pady=12)
 text=tk.Text(window,width=90,height=16,undo=False);text.pack(padx=12,pady=12)
 text.insert('1.0',json.dumps(result,ensure_ascii=False,indent=2));text.configure(state='disabled')
 window.mainloop()

if __name__=='__main__':
 try:main()
 except Exception:pass
