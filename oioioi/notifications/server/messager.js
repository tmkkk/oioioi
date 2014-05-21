var rabbit = require('rabbit.js');
var getQueueNameForUser = require('./queuemanager.js').getQueueNameForUser;
var context;

/* This is a test client of OIOIOI messaging.
* It was created to help test OIOIOI message events without
* triggering actual functions emitting notifications.
*
* It emits notifications to an OIOIOI registered user using his user name.
* You can provide your own AMQP backend url with last parameter.
*
* */
function getGuid() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random()*16|0, v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}


function emit(target, message) {
    target = getQueueNameForUser(target);
    console.log('Emitting "' + message + '" to "' + target + '"...');
    var push = context.socket('PUSH');
    push.connect(target, function() {

        /* ID should be unique. Normally it is generated by OIOIOI instance, but here it is
           an "approximation" of uniqueness just for demo/testing. Just don't call it more frequently
           than once in a millisecond and it will work fine.
         */
        push.write(JSON.stringify({id: getGuid(), message: message}), 'utf8', function() {console.log('writtern');});
        push.end();
        setTimeout(process.exit, 200);
    });
}

function messager() {
    console.log('OIOIOI Messager');
    if (process.argv.length !== 4) {
        console.log("Usage: node messager.js target-user-id message [amqp]");
        return;
    }
    var target = process.argv[2];
    var message = process.argv[3];
    var amqp = process.argv[4] ? process.argv[4] : 'amqp://localhost';
    context = rabbit.createContext(amqp);
    context.on("ready", function() {
        emit(target, message);
    });
}

messager();