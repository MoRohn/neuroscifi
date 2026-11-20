import {forecast,outcomes} from "../../src/neuroscifi/pocket/web/field-model.mjs";
const stats={trials:0,answers:0,correct:0,baselineCorrect:0,modelLoss:0,baselineLoss:0};
for(let stream=1;stream<=48;stream++){
 let state=stream;const rand=()=>{state=(1664525*state+1013904223)>>>0;return state/4294967296;};
 const rows=[],counts=[1,1,1];const offset=stream%3;
 for(let i=0;i<80;i++){
  const setting=Math.floor(rand()*3), outcome=rand()<.18?Math.floor(rand()*3):(setting+offset)%3;
  const features={goal:["work","owner"]};
  if(rand()>.18)features.sound=[["soft","moderate","strong"][setting],"mic"];
  if(rand()>.25)features.switches=[["few","some","many"][setting],"android"];
  const result=forecast(rows,features), label=outcomes[outcome];
  if(i>=16){
   stats.trials++;stats.modelLoss-=Math.log(result.probs[label]);stats.baselineLoss-=Math.log(counts[outcome]/counts.reduce((a,b)=>a+b));
   if(result.answer){stats.answers++;stats.correct+=Number(result.answer===label);
      stats.baselineCorrect+=Number(counts.indexOf(Math.max(...counts))===outcome);}
  }
  rows.push({id:stream+"-"+i,created:i,features,outcome:label,learn:true});counts[outcome]++;
  if(rows.length>64){const old=rows.shift();counts[outcomes.indexOf(old.outcome)]--;}
 }
}
console.log(JSON.stringify({...stats,modelLogLoss:stats.modelLoss/stats.trials,baselineLogLoss:stats.baselineLoss/stats.trials,coverage:stats.answers/stats.trials,selectiveAccuracy:stats.correct/stats.answers,baselineOnAnswered:stats.baselineCorrect/stats.answers},null,2));
