package gh.fineflow.bridge;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.Bundle;
import android.provider.Telephony;
import android.text.InputType;
import android.widget.*;
import androidx.work.WorkManager;
import java.net.URI;

public class MainActivity extends Activity {
    EditText url,token,senders,keywords;
    CheckBox consent;
    TextView status;
    LinearLayout column;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll=new ScrollView(this); column=new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL); column.setPadding(32,40,32,40); scroll.addView(column); setContentView(scroll);
        TextView title=new TextView(this); title.setText("FineFlow Bridge"); title.setTextSize(28); column.addView(title);
        label("Owner phone setup · Android pilot\nPrepared for Percy Kpodo and Copson\n\nOnly messages matching BOTH the selected senders and keywords are forwarded to your assigned administrator. Your phone needs internet access. This app does not verify fines or make payments.");
        url=field("Server address, https://...",Bridge.prefs(this).getString("url",""),false);
        token=field("Device token, provided by your administrator","",true);
        label("Leave token blank to keep the saved token.");
        senders=field("Allowed SMS senders, separated by commas",Bridge.prefs(this).getString("senders",""),false);
        keywords=field("Notice keywords, separated by commas",Bridge.prefs(this).getString("keywords","fine,penalty,offence,offense"),false);
        consent=new CheckBox(this); consent.setText("I own or am authorized to manage this phone. I consent to matching SMS notices being shared with my assigned administrator.");
        consent.setChecked(Bridge.prefs(this).getBoolean("enabled",false)); column.addView(consent);
        button("Save and enable forwarding",()->save());
        button("Stop forwarding and clear queued notices",()->{
            Bridge.prefs(this).edit().putBoolean("enabled",false).apply();
            WorkManager.getInstance(this).cancelAllWork(); consent.setChecked(false);
            Bridge.status(this,"Stopped. Previously submitted notices remain on the server."); refresh();
        });
        button("Request access for NEW SMS",()->requestPermissions(new String[]{Manifest.permission.RECEIVE_SMS},1));
        label("New incoming SMS are checked automatically after permission is granted. Earlier messages are scanned only when you press the next button, limited to the last 7 days and 500 messages. Android may delay background work.");
        button("Scan matching SMS from the last 7 days",()->{
            if(checkSelfPermission(Manifest.permission.READ_SMS)!=PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.READ_SMS},2);
                Bridge.status(this,"After granting inbox access, press Scan again."); refresh(); return;
            }
            new Thread(()->scan()).start();
        });
        button("Refresh status",()->refresh());
        status=new TextView(this); status.setPadding(0,24,0,24); column.addView(status); refresh();
    }
    private void label(String text) { TextView t=new TextView(this); t.setText(text); t.setPadding(0,16,0,16); column.addView(t); }
    private EditText field(String hint,String value,boolean password) {
        EditText e=new EditText(this); e.setHint(hint); e.setText(value); e.setSingleLine(true);
        if(password) e.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        column.addView(e); return e;
    }
    private void button(String text,Runnable action) { Button b=new Button(this); b.setText(text); b.setOnClickListener(v->action.run()); column.addView(b); }
    private void refresh() { if(status!=null) status.setText((Bridge.prefs(this).getBoolean("enabled",false)?"Forwarding enabled\n":"Forwarding stopped\n")+Bridge.prefs(this).getString("status","No notices submitted yet.")); }
    private void save() {
        try {
            String address=url.getText().toString().trim().replaceAll("/+$",""); URI uri=new URI(address);
            if(!"https".equals(uri.getScheme()) || uri.getHost()==null || uri.getRawUserInfo()!=null || uri.getRawQuery()!=null || uri.getRawFragment()!=null || !(uri.getPath().isEmpty()||uri.getPath().equals("/"))) throw new Exception();
            if(!consent.isChecked() || senders.getText().toString().trim().isEmpty() || keywords.getText().toString().trim().isEmpty()) throw new Exception();
            String encrypted=Bridge.prefs(this).getString("token","");
            if(!token.getText().toString().trim().isEmpty()) encrypted=Bridge.encrypt(token.getText().toString().trim());
            if(encrypted.isEmpty()) throw new Exception();
            Bridge.prefs(this).edit().putString("url",address).putString("token",encrypted)
                .putString("senders",senders.getText().toString()).putString("keywords",keywords.getText().toString()).putBoolean("enabled",true).apply();
            token.setText(""); Bridge.status(this,"Saved. Grant NEW SMS access to receive incoming notices."); refresh();
        } catch(Exception e) { Toast.makeText(this,"Enter an HTTPS server, token, sender filters, keywords and consent.",Toast.LENGTH_LONG).show(); }
    }
    private void scan() {
        int scanned=0,matching=0;
        try(Cursor cursor=getContentResolver().query(Telephony.Sms.Inbox.CONTENT_URI,new String[]{"address","body","date"},"date >= ?",new String[]{Long.toString(System.currentTimeMillis()-7L*24*60*60*1000)},"date DESC")) {
            if(cursor!=null) while(scanned<500 && cursor.moveToNext()) {
                scanned++; String sender=cursor.getString(0),body=cursor.getString(1); long time=cursor.getLong(2);
                if(sender!=null && body!=null && Bridge.matches(this,sender,body)) { Bridge.enqueue(this,sender,body,time); matching++; }
            }
            Bridge.status(this,"Scan complete: "+scanned+" checked, "+matching+" matching notices queued. Server prevents duplicates.");
        } catch(Exception e) { Bridge.status(this,"Inbox scan failed. Check permissions and saved setup."); }
        runOnUiThread(()->refresh());
    }
}
