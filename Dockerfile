# Imagem única para todos os serviços (nós e contador).
# Cada serviço do compose.yaml só muda o comando.
FROM python:3.12-slim

WORKDIR /app

# Mostra os prints na hora (sem buffer) e não gera __pycache__ no contêiner
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Só o código que roda dentro dos contêineres; o resto fica fora da imagem
COPY node/ /app/node/
COPY counter/ /app/counter/

CMD ["python", "-m", "node"]
