# Calendário Econômico

Um único calendário iCalendar (`.ics`) com a agenda que importa para o dia a dia de quem opera no Brasil: Banco Central, divulgações do IBGE, PMI Brasil, decisão e ata do Fed, CPI e payroll dos Estados Unidos. Assine uma vez no iPhone ou no Mac. As datas entram sozinhas e acompanham as atualizações.

**Link da assinatura (o mesmo de antes):**

https://raw.githubusercontent.com/cvsezefredo-sudo/CALENDARIO-ECONOMICO/main/calendario-bcb.ics

O mesmo arquivo também está em `calendario-economico.ics`, com o mesmo conteúdo. Quem já assinou o link antigo não precisa trocar nada. No celular o calendário passa a aparecer como **Calendário Econômico**.

Cada compromisso começa com uma etiqueta da origem: `[Copom]`, `[Focus]`, `[Fiscais]`, `[Macro]`, `[Monetárias]`, `[Externo]`, `[Aberto]`, `[Receber]`, `[IBC-Br]`, `[IBCR]`, `[IC-Br]`, `[Pol. Monetária]`, `[Estabilidade]`, `[Investimento]`, `[Regional]`, `[Capitais]`, `[CMN]`, `[Comef]`, `[Coremec]`, `[GRC]`, `[Indeco]`, `[Ranking]`, `[Sondagens]`, `[PTC]`, `[Eventos]`, `[IBGE]`, `[PMI]`, `[Fed]`, `[BLS]`.

Exemplos: `[IBGE] IPCA – outubro/2026`, `[Fed] Decisão de juros (FOMC)`, `[PMI] PMI Industrial Brasil – novembro/2026`, `[BLS] CPI dos EUA – outubro/2026`, `[Copom] Decisão de juros (Copom)`.

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

## O que entra

- **Banco Central.** Os mesmos feeds de sempre: Copom, Focus, estatísticas, relatórios, sondagens e eventos. A decisão de juros (segundo dia da reunião) fica às **18:30** de Brasília. O arquivo do BC marca esse dia à meia-noite; o script corrige. O primeiro dia vira compromisso de dia inteiro, para não tocar alarme à meia-noite. Reuniões antigas que já vinham com horário ficam como o BC publicou.
- **IBGE.** Tudo o que a API oficial de calendário listar no período (IPCA, IPCA-15, PIB trimestral, PNAD Contínua, produção industrial, vendas no varejo, volume de serviços e o restante). O IPCA e os outros indicadores saem às **09:00** de Brasília quando a API manda 12:00 UTC.
- **PMI Brasil (S&P Global).** Industrial e serviços. Quando o calendário oficial traz a data, ela entra com o horário publicado (13:00 UTC, **10:00** em Brasília). Os meses que a página ainda não listou seguem a regra da própria S&P: industrial no 1º dia útil, serviços no 3º dia útil, 10:00. Esses ficam marcados como **data estimada** na descrição.
- **Fed.** Decisão de juros no segundo dia da reunião do FOMC e a ata, no horário do arquivo `calendar.json` do Federal Reserve (em geral 14:00 no Leste dos EUA), convertido para Brasília. Em dezembro, 14:00 de Nova York viram **16:00** aqui; no horário de verão americano, **15:00**.
- **BLS.** CPI e Employment Situation (payroll), no cronograma oficial, 08:30 no Leste dos EUA convertidos para Brasília.

## Atualização

O arquivo é refeito a cada hora, no minuto 17 (UTC), por um workflow do GitHub Actions. Ele também pode ser disparado manualmente. O commit só entra quando algum evento mudou. O calendário pede aos aplicativos para buscar de novo a cada 1 hora (`PT1H`).

Se uma fonte falhar, as outras continuam. A que falhou reaproveita a última coleta boa, guardada em `data/cache/`. Se a geração inteira vier vazia ou inválida, o arquivo anterior permanece publicado.

Para gerar localmente, na raiz do repositório:

```bash
python3 scripts/merge_bcb_ics.py
python3 scripts/test_calendario.py
```

O resultado fica em `calendario-bcb.ics` e na cópia `calendario-economico.ics`.

## Limites das fontes

- A API do IBGE (`servicodados.ibge.gov.br/api/v3/calendario`) publica o horário em UTC. 12:00 UTC é 09:00 em Brasília; 13:00 UTC é 10:00. A descrição de cada evento lembra essa conversão.
- O calendário público da S&P só mostra os próximos dias. O resto do PMI é estimado. A conta de dia útil pula fim de semana, feriado nacional, segunda e terça de carnaval, sexta-feira santa e Corpus Christi. A S&P pode não observar algum desses feriados; quando a página oficial listar o mês, essa data substitui a estimativa.
- O site do BLS às vezes recusa acesso automático. O script tenta as páginas de CPI e payroll e o arquivo `bls.ics`. Se os três falharem, fica a coleta anterior.
- O `19700101` que existia no arquivo era só a âncora do fuso horário, não um compromisso. Agora essa âncora é `19900101`. Evento com data ilegível, ou com ano anterior a 1990 (inclusive o epoch `19700101`, que alguns apps mostram quando não conseguem ler a data), é descartado e aparece no log.
- Não usamos Investing.com.
