package org.neuroscifi.pocket;

import android.app.Activity;
import android.app.AppOpsManager;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.Intent;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.WindowManager;
import android.widget.*;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import org.json.*;

public class Metrics extends Activity {
    private TextView summary;
    private CheckBox usage, system;
    private Button export;
    private EditText target;
    private String preview;

    public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        LinearLayout root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);root.setPadding(32,32,32,32);
        ScrollView scroll=new ScrollView(this);scroll.addView(root);setContentView(scroll);
        TextView title=new TextView(this);title.setText("Pocket Metrics");title.setTextSize(28);root.addView(title);
        usage=new CheckBox(this);usage.setText("App changes and screen activations over the last hour");root.addView(usage);
        target=new EditText(this); target.setSingleLine(true);target.setHint("Optional: app package to measure locally");
        target.setImportantForAutofill(android.view.View.IMPORTANT_FOR_AUTOFILL_NO);
        target.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        root.addView(target);
        system=new CheckBox(this);system.setText("Current power-saving and thermal state");root.addView(system);
        Button permission=new Button(this);permission.setText("Open Android usage access settings");root.addView(permission);
        permission.setOnClickListener(v->startActivity(new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)));
        Button sample=new Button(this);sample.setText("Review selected metrics");root.addView(sample);
        summary=new TextView(this);root.addView(summary);
        export=new Button(this);export.setText("Save reviewed JSON");export.setEnabled(false);root.addView(export);
        target.addTextChangedListener(new android.text.TextWatcher(){
            public void beforeTextChanged(CharSequence s,int start,int count,int after){}
            public void onTextChanged(CharSequence s,int start,int before,int count){clear();}
            public void afterTextChanged(android.text.Editable s){}
        });
        usage.setOnCheckedChangeListener((v,on)->clear());system.setOnCheckedChangeListener((v,on)->clear());
        sample.setOnClickListener(v->read());
        export.setOnClickListener(v->{ Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/json").putExtra(Intent.EXTRA_TITLE,"pocket-device.json");startActivityForResult(intent,10); });
    }
    private void clear(){preview=null;export.setEnabled(false);summary.setText("");}
    private static String band(int n){return n<=2?"few":n<=8?"some":"many";}
    private static String duration(long ms){return ms<300000?"under5":ms<1200000?"5to20":"20plus";}
    private static void put(JSONObject obj,String k,String v)throws JSONException{
        obj.put(k,new JSONArray().put(v).put("android"));
    }
    private boolean allowed(){
        AppOpsManager ops=getSystemService(AppOpsManager.class);
        return ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS,android.os.Process.myUid(),getPackageName())==AppOpsManager.MODE_ALLOWED;
    }
    private void read(){
        clear(); JSONObject f=new JSONObject();String note="";
        try {
            if(system.isChecked()){
                PowerManager p=getSystemService(PowerManager.class);
                put(f,"power",p.isPowerSaveMode()?"saving":"standard");
                int t=p.getCurrentThermalStatus();
                put(f,"thermal",t>=PowerManager.THERMAL_STATUS_SEVERE?"high":t>=PowerManager.THERMAL_STATUS_MODERATE?"elevated":"normal");
            }
            if(usage.isChecked() && allowed()){
                long end=System.currentTimeMillis();
                UsageEvents events=getSystemService(UsageStatsManager.class).queryEvents(end-3600000L,end);
                String desired=target.getText().toString().trim();
                long screenStart=-1,appStart=-1,screenMs=0,appMs=0;
                int screenPairs=0,appPairs=0;
                int changes=0, wake=0, resumes=0; boolean hasAny=false;String last=null;
                UsageEvents.Event event=new UsageEvents.Event();
                while(events!=null && events.hasNextEvent()){
                    events.getNextEvent(event);hasAny=true;
                    long stamp=event.getTimeStamp();
                    int type=event.getEventType();
                    if(type==UsageEvents.Event.SCREEN_INTERACTIVE){wake++;if(screenStart<0)screenStart=stamp;}
                    if(type==UsageEvents.Event.SCREEN_NON_INTERACTIVE){
                        if(screenStart>=0 && stamp>=screenStart){screenMs+=stamp-screenStart;screenPairs++;}
                        screenStart=-1;appStart=-1;
                    }
                    if(type==UsageEvents.Event.DEVICE_SHUTDOWN || type==UsageEvents.Event.DEVICE_STARTUP){
                        screenStart=-1;appStart=-1;last=null;
                    }
                    if(!desired.isEmpty() && desired.equals(event.getPackageName())){
                        if(type==UsageEvents.Event.ACTIVITY_RESUMED && appStart<0)appStart=stamp;
                        if(type==UsageEvents.Event.ACTIVITY_PAUSED && appStart>=0){
                            if(stamp>=appStart){appMs+=stamp-appStart;appPairs++;}
                            appStart=-1;
                        }
                    }else if(type==UsageEvents.Event.ACTIVITY_RESUMED)appStart=-1;
                    if(event.getEventType()==UsageEvents.Event.SCREEN_NON_INTERACTIVE) last=null;
                    if(event.getEventType()==UsageEvents.Event.ACTIVITY_RESUMED && event.getPackageName()!=null){
                        String current=event.getPackageName();resumes++;
                        if(last!=null&&!last.equals(current))changes++;
                        last=current;
                    }
                }
                if(screenPairs>0)put(f,"screenTime",duration(screenMs));
                if(appPairs>0)put(f,"appTime",duration(appMs));
                if(resumes>0)put(f,"switches",band(changes));
                if(hasAny)put(f,"wakes",band(wake));
                if(!hasAny)note="No event coverage. ";
            } else if(usage.isChecked())note="Usage access unavailable. ";
            if(f.length()==0){summary.setText(note+"No eligible metrics.");return;}
            preview=new JSONObject().put("schema","pocket-device-1").put("features",f).toString();
            summary.setText(note+preview);export.setEnabled(true);
        } catch(Exception ex){clear();summary.setText("Metrics unavailable. Review permissions and retry.");}
    }
    protected void onActivityResult(int req,int result,Intent data){
        super.onActivityResult(req,result,data);
        if(req!=10||result!=RESULT_OK||data==null||data.getData()==null||preview==null)return;
        if(usage.isChecked()&&!allowed()){clear();summary.setText("Usage grant changed. Review again.");return;}
        try(OutputStream out=getContentResolver().openOutputStream(data.getData(),"wt")){
            out.write(preview.getBytes(StandardCharsets.UTF_8));clear();summary.setText("Saved. Import the receipt in Pocket.");
        } catch(Exception ex){clear();summary.setText("Export unavailable.");}
    }
}
