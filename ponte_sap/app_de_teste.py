"""App Tk de teste no lugar do AutomacaoSAP (teste_ponte_sap.py): grava o que acontece num JSON."""
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox

ESTADO = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'estado.json')
estado = {'enter': 0, 'cliques': 0, 'entrada': '', 'texto': '', 'resposta': None, 'lista': None, 'rolou': 0, 'tamanho': None}


def gravar():
    with open(ESTADO, 'w', encoding='utf-8') as f:
        json.dump(estado, f)


raiz = tk.Tk()
raiz.title('Automação SAP — teste')
raiz.geometry('720x480+200+200')
raiz.configure(bg='#F6F1FA')
rotulo = tk.Label(raiz, text='Cliques: 0', font=('Segoe UI', 14), bg='#F6F1FA', fg='#5B2C83')
rotulo.pack(pady=10)


def somar():
    estado['cliques'] += 1
    rotulo.config(text=f'Cliques: {estado["cliques"]}')
    gravar()


tk.Button(raiz, text='Somar', command=somar, font=('Segoe UI', 12), bg='#7B3FA8', fg='white').pack()
entrada = tk.Entry(raiz, font=('Segoe UI', 12), width=30)
entrada.pack(pady=8)
entrada.bind('<KeyRelease>', lambda e: (estado.__setitem__('entrada', entrada.get()), gravar()))
entrada.bind('<Return>', lambda e: (estado.__setitem__('enter', estado['enter'] + 1), gravar()))


def perguntar():
    estado['resposta'] = messagebox.askyesno('Confirmação', 'Gravar no SAP?')
    gravar()


tk.Button(raiz, text='Perguntar', command=perguntar).pack()
lista = tk.Listbox(raiz, height=6)
for i in range(60):
    lista.insert('end', f'Item {i:02d}')
lista.pack(pady=8)
lista.bind('<<ListboxSelect>>', lambda e: (estado.__setitem__('lista', lista.get(lista.curselection()[0]) if lista.curselection() else None), gravar()))
lista.bind('<MouseWheel>', lambda e: (estado.__setitem__('rolou', estado['rolou'] + 1), gravar()), add='+')
raiz.bind('<Configure>', lambda e: (estado.__setitem__('tamanho', [raiz.winfo_width(), raiz.winfo_height()]), gravar()) if e.widget is raiz else None)
botoes = {}
for w_ in raiz.winfo_children():
    if isinstance(w_, tk.Button):
        botoes[w_.cget('text')] = w_
botoes['entrada'], botoes['lista'] = entrada, lista


def posicoes():
    estado['pos'] = {k: [v.winfo_rootx() - raiz.winfo_rootx(), v.winfo_rooty() - raiz.winfo_rooty(), v.winfo_width(), v.winfo_height()]
                     for k, v in botoes.items()}
    gravar()
    raiz.after(500, posicoes)


raiz.after(300, posicoes)
gravar()
raiz.mainloop()
