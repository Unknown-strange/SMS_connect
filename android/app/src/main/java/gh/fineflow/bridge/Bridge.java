package gh.fineflow.bridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import androidx.work.*;
import org.json.JSONObject;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

final class Bridge {
    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("fineflow", Context.MODE_PRIVATE); }
    static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (!store.containsAlias("fineflow")) {
            KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            gen.init(new KeyGenParameterSpec.Builder("fineflow",KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            gen.generateKey();
        }
        return (SecretKey) store.getKey("fineflow",null);
    }
    static String encrypt(String s) throws Exception {
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.ENCRYPT_MODE,key());
        return Base64.encodeToString(c.getIV(),Base64.NO_WRAP)+":"+Base64.encodeToString(c.doFinal(s.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
    }
    static String decrypt(String s) throws Exception {
        String[] a=s.split(":",2); Cipher c=Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(a[0],Base64.NO_WRAP)));
        return new String(c.doFinal(Base64.decode(a[1],Base64.NO_WRAP)),StandardCharsets.UTF_8);
    }
    static boolean matches(Context c,String sender,String body) {
        SharedPreferences p=prefs(c);
        if (!p.getBoolean("enabled",false)) return false;
        boolean allowed=false;
        for(String s:p.getString("senders","").split(",")) if(!s.trim().isEmpty() && s.trim().equalsIgnoreCase(sender.trim())) allowed=true;
        if(!allowed) return false;
        for(String s:p.getString("keywords","").split(",")) if(!s.trim().isEmpty() && body.toLowerCase(Locale.ROOT).contains(s.trim().toLowerCase(Locale.ROOT))) return true;
        return false;
    }
    static void enqueue(Context c,String sender,String body,long time) throws Exception {
        if(!matches(c,sender,body) || body.length()>3000) return;
        byte[] hash=MessageDigest.getInstance("SHA-256").digest((sender+"\n"+time+"\n"+body).getBytes(StandardCharsets.UTF_8));
        StringBuilder id=new StringBuilder(); for(byte b:hash) id.append(String.format(Locale.ROOT,"%02x",b&255));
        JSONObject event=new JSONObject().put("event_id",id.toString()).put("sender",sender).put("body",body).put("received_at",time);
        // Bind pending work to this configuration. A later route change must not reroute an old SMS.
        JSONObject envelope=new JSONObject().put("event",event).put("url",prefs(c).getString("url",""))
            .put("token",decrypt(prefs(c).getString("token","")));
        if(envelope.toString().getBytes(StandardCharsets.UTF_8).length>6500) { status(c,"Notice too long to queue. Review it manually."); return; }
        Data input=new Data.Builder().putString("encrypted",encrypt(envelope.toString())).build();
        OneTimeWorkRequest work=new OneTimeWorkRequest.Builder(ForwardWorker.class).setInputData(input)
            .setConstraints(new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL,30,TimeUnit.SECONDS).build();
        WorkManager.getInstance(c).enqueueUniqueWork("sms-"+id,ExistingWorkPolicy.KEEP,work);
    }
    static void status(Context c,String s) { prefs(c).edit().putString("status",s).apply(); }
}
