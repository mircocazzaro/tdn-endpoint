FROM openjdk:11-jre-slim
WORKDIR /ontop

COPY ../myapp/obda/ontop ./ontop
COPY ../myapp/obda/lib ./lib
COPY ../myapp/obda/jdbc ./jdbc
COPY ../myapp/obda/*.properties ./
COPY ../myapp/obda/*.ttl ./
COPY ../myapp/obda/*.duckdb ./

RUN chmod +x ontop
EXPOSE 8080
CMD ["./ontop","endpoint","--properties","hereditary_ontology_2.properties","--port","8080"]
