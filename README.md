# Calendário Econômico BCB

Um único calendário iCalendar (`.ics`) que reúne os calendários públicos do Banco Central do Brasil: Copom, Focus, Comef, estatísticas, relatórios, sondagens e eventos. Assine uma vez no iPhone ou no Mac; as datas entram sozinhas e passam a acompanhar as atualizações do BC.

**Link da assinatura:**

https://raw.githubusercontent.com/cvsezefredo-sudo/CALENDARIO-ECONOMICO/main/calendario-bcb.ics

O calendário aparece como **Calendário Econômico BCB**. Cada compromisso começa com uma etiqueta curta, para dar para ver a origem no meio da agenda: `[Copom]`, `[Focus]`, `[Fiscais]`, `[Macro]`, `[Monetárias]`, `[Externo]`, `[Aberto]`, `[Receber]`, `[IBC-Br]`, `[IBCR]`, `[IC-Br]`, `[Pol. Monetária]`, `[Estabilidade]`, `[Investimento]`, `[Regional]`, `[Capitais]`, `[CMN]`, `[Comef]`, `[Coremec]`, `[GRC]`, `[Indeco]`, `[Ranking]`, `[Sondagens]`, `[PTC]`, `[Eventos]`.

## iPhone

1. Abra **Ajustes**.
2. Toque em **Calendário**.
3. Toque em **Contas**.
4. Toque em **Adicionar Conta**.
5. Toque em **Outra**.
6. Toque em **Adicionar Calendário Assinado**.
7. Cole o link acima e confirme.

## Mac

1. Abra o app **Agenda**.
2. No menu, escolha **Arquivo** > **Nova Assinatura de Calendário**.
3. Cole o link acima e confirme.

## Atualização

O arquivo é refeito a cada hora, no minuto 17 (UTC), por um workflow do GitHub Actions. Ele também pode ser disparado manualmente. O commit só entra quando algum evento mudou. O calendário pede aos aplicativos para buscar de novo a cada 1 hora (`PT1H`).

A lista de feeds vem da página de calendários ICS do Banco Central. Se essa página falhar, o script usa uma lista fixa de apoio. Um feed que falhe fica de fora daquela rodada; os demais continuam no arquivo. Se a geração vier vazia ou inválida, o arquivo anterior permanece publicado.

Para gerar localmente, na raiz do repositório:

```bash
python3 scripts/merge_bcb_ics.py
```

O resultado fica em `calendario-bcb.ics`.
