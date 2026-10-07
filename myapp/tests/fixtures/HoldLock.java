import java.io.FileInputStream;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.Properties;

/**
 * Apre il database come lo apre Ontop e tiene la connessione finche' non legge
 * una riga da stdin.
 *
 * Argomenti: <file .properties di Ontop> <percorso del database>
 *
 * Dal file .properties si leggono jdbc.driver e tutte le proprieta' con
 * prefisso jdbc.property., passate al driver senza prefisso, come fa Ontop.
 * L'URL viene ricostruito sul percorso dato, cosi' da lavorare su una copia.
 */
public class HoldLock {
    public static void main(String[] args) throws Exception {
        Properties ontop = new Properties();
        try (FileInputStream in = new FileInputStream(args[0])) {
            ontop.load(in);
        }
        Class.forName(ontop.getProperty("jdbc.driver").trim());

        Properties jdbc = new Properties();
        for (String name : ontop.stringPropertyNames()) {
            if (name.startsWith("jdbc.property.")) {
                jdbc.setProperty(name.substring("jdbc.property.".length()),
                                 ontop.getProperty(name).trim());
            }
        }
        Connection c = DriverManager.getConnection("jdbc:duckdb:" + args[1], jdbc);
        System.out.println("READY " + jdbc);
        System.out.flush();
        System.in.read();
        c.close();
    }
}
