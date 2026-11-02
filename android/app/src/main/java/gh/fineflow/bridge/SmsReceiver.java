package gh.fineflow.bridge;
import android.content.*;
import android.provider.Telephony;
import android.telephony.SmsMessage;
import java.util.LinkedHashMap;

public class SmsReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent) {
        if(!Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(intent.getAction())) return;
        SmsMessage[] parts=Telephony.Sms.Intents.getMessagesFromIntent(intent);
        LinkedHashMap<String,StringBuilder> bodies=new LinkedHashMap<>();
        LinkedHashMap<String,Long> times=new LinkedHashMap<>();
        for(SmsMessage m:parts) {
            String sender=m.getOriginatingAddress(); if(sender==null) continue;
            bodies.computeIfAbsent(sender,k->new StringBuilder()).append(m.getMessageBody());
            times.putIfAbsent(sender,m.getTimestampMillis());
        }
        for(String sender:bodies.keySet()) try { Bridge.enqueue(context,sender,bodies.get(sender).toString(),times.get(sender)); }
        catch(Exception e) { Bridge.status(context,"Could not queue notice. Open app and check setup."); }
    }
}
