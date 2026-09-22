"""Quiz gamificado — perguntas e respostas ao vivo, no estilo do Kahoot.

Para treinamentos, desafios e competições entre os colaboradores:

1. Quem tem a permissão "Criar e administrar quizzes" (SUPERADMIN e quem ele
   liberar na tela do usuário) cria o quiz: nome, descrição, categoria/tema e as
   perguntas — cada uma com 2 a 4 alternativas, uma correta, e o tempo para
   responder. Dá para editar, duplicar, reordenar e excluir perguntas enquanto o
   quiz é rascunho, e reaproveitar perguntas de outros quizzes (banco de perguntas).
   Publicado, o quiz trava (para mudar, duplica).
2. Com o quiz publicado, cria a sala: data e horário, um código próprio (6
   letras/números) e quem participa — por pessoa, loja, cargo, setor, grupo ou
   coordenação. Cada participante recebe uma notificação no portal com o nome
   do quiz, a data, o horário e o link da sala.
3. Os participantes entram com o próprio usuário, em "Quiz" no menu (ou pelo
   código da sala). O responsável vê quem já entrou e inicia quando quiser.
4. Todos recebem a mesma pergunta ao mesmo tempo. Cada um escolhe uma
   alternativa e confirma — depois não muda. O tempo de cada pergunta conta de
   quando ela chega na tela da pessoa; acabou o tempo, fica sem resposta.
5. Pontos: só quem acerta pontua — 1000 respondendo na hora, até 500 no último
   instante (quanto mais rápido, mais pontos). Errou ou não respondeu: zero.
6. Depois de cada pergunta aparece a resposta certa, quantos marcaram cada
   alternativa e o ranking. No fim, o pódio e o ranking final.
7. Cada participante vê o próprio resultado (acertos, erros, pontos, posição e
   quais perguntas acertou). O responsável vê todos: quem participou e quem não,
   acertos, erros, pontos, posição e as perguntas com mais erros — e exporta
   para Excel. Os quizzes realizados ficam no histórico e nos relatórios.
"""
