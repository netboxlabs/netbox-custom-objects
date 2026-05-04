// GraphQL Cache Clearer - Run this in your browser console
// This will clear all GraphiQL cached data and force a fresh schema load

console.log('🧹 Starting GraphQL cache clear...');

// Clear localStorage
if (window.localStorage) {
    let cleared = 0;
    Object.keys(window.localStorage).forEach(key => {
        if (key.includes('graphiql') || key.includes('graphql') || key.includes('schema')) {
            window.localStorage.removeItem(key);
            cleared++;
        }
    });
    console.log(`✅ Cleared ${cleared} items from localStorage`);
} else {
    console.log('❌ localStorage not available');
}

// Clear sessionStorage
if (window.sessionStorage) {
    let cleared = 0;
    Object.keys(window.sessionStorage).forEach(key => {
        if (key.includes('graphiql') || key.includes('graphql') || key.includes('schema')) {
            window.sessionStorage.removeItem(key);
            cleared++;
        }
    });
    console.log(`✅ Cleared ${cleared} items from sessionStorage`);
}

// Clear any cached data in memory
if (window.GraphiQL && window.GraphiQL.clearCache) {
    window.GraphiQL.clearCache();
    console.log('✅ Cleared GraphiQL memory cache');
}

// Test the current GraphQL schema
async function testSchema() {
    try {
        const response = await fetch('/graphql/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            },
            body: JSON.stringify({
                query: '{ __schema { queryType { fields { name } } } }'
            })
        });

        const data = await response.json();
        
        if (data.data && data.data.__schema) {
            const fields = data.data.__schema.queryType.fields;
            const customFields = fields.filter(field => 
                field.name.includes('_list') || 
                field.name.includes('cat') || 
                field.name.includes('dog') ||
                field.name.includes('firstobject') ||
                field.name.includes('secondobject') ||
                field.name.includes('foo')
            );
            
            const hasFoo = customFields.some(field => field.name.includes('foo'));
            
            if (hasFoo) {
                console.log('❌ ERROR: "foo" resolvers still found in schema!');
                console.log('Custom object fields:', customFields.map(f => f.name));
            } else {
                console.log('✅ SUCCESS: No "foo" resolvers found. Schema is clean!');
                console.log('Custom object fields:', customFields.map(f => f.name));
            }
        } else {
            console.log('❌ Error: Could not get schema data');
        }
    } catch (error) {
        console.log('❌ Error testing GraphQL:', error.message);
    }
}

// Run the test
testSchema();

console.log('🎉 Cache clear complete! Now refresh the GraphiQL page (Ctrl+Shift+R or Cmd+Shift+R)');

