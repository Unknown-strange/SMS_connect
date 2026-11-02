package gh.fineflow.bridge;
import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.*;
import org.json.JSONObject;
import java.net.*;
import javax.net.ssl.HttpsURLConnection;
import java.nio.charset.StandardCharsets;
import java.io.*;

public class ForwardWorker extends Worker {
    public ForwardWorker(@NonNull Context c,@NonNull WorkerParameters p) { super(c,p); }
    @NonNull @Override public Result doWork() {
        Context c=getApplicationContext();
        if(!Bridge.prefs(c).getBoolean("enabled",false)) return Result.success();
        HttpsURLConnection connection=null;
        try {
            JSONObject envelope=new JSONObject(Bridge.decrypt(getInputData().getString("encrypted")));
            URL url=new URL(envelope.getString("url")+"/v1/notices");
            if(!"https".equals(url.getProtocol())) throw new IllegalArgumentException();
            connection=(HttpsURLConnection)url.openConnection();
            connection.setInstanceFollowRedirects(false); connection.setRequestMethod("POST");
            connection.setConnectTimeout(15000); connection.setReadTimeout(30000); connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type","application/json");
            connection.setRequestProperty("Authorization","Bearer "+envelope.getString("token"));
            try(OutputStream out=connection.getOutputStream()) { out.write(envelope.getJSONObject("event").toString().getBytes(StandardCharsets.UTF_8)); }
            int code=connection.getResponseCode();
            if(code==429 || code>=500) return retryOrFail(c);
            if(code!=200) { Bridge.status(c,"Forwarding blocked: HTTP "+code+". Check device setup."); return Result.failure(); }
            StringBuilder response=new StringBuilder();
            try(BufferedReader in=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))) {
                String line; while((line=in.readLine())!=null && response.length()<16000) response.append(line);
            }
            String status=new JSONObject(response.toString()).getString("status");
            Bridge.status(c,"Last server result: "+status+". Accepted does not mean delivered.");
            return Result.success();
        } catch(IOException e) { return retryOrFail(c); }
        catch(Exception e) { Bridge.status(c,"Setup or encryption error. Re-save configuration."); return Result.failure(); }
        finally { if(connection!=null) connection.disconnect(); }
    }
    private Result retryOrFail(Context c) {
        if(getRunAttemptCount()>=10) { Bridge.status(c,"Network retries exhausted. Review notice on phone."); return Result.failure(); }
        Bridge.status(c,"Waiting to retry when connection is available."); return Result.retry();
    }
}
